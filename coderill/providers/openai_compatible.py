"""The model client.

This wraps an OpenAI-compatible chat completions endpoint and hides the parts
that actually make calling an LLM annoying in practice:

1. Streaming tool calls. When the model decides to call a tool, the arguments
   arrive as a series of JSON fragments split across chunks; we glue them back
   together in order before handing the finished call to the loop.
2. Transient failures. Rate limits (429), timeouts, dropped connections and 5xx
   responses are retried with exponential backoff. Everything else fails fast.
3. Deadlines. Browser tasks run under a wall-clock budget, so both entry points
   accept a ``deadline`` and refuse to start work past it.

Two entry points:

- :meth:`LLM.stream` -- the agent loop's path; yields text/reasoning deltas and
  ends with the finished assistant message.
- :meth:`LLM.chat` -- a plain non-streaming call, used by the browser task's text
  helper, returning a :class:`Response`.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from ..config import Config

# HTTP statuses worth retrying. Everything else (400, 401, 404, ...) is a bug in
# how we are calling the API or a bad key, and retrying will not help.
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}

MAX_BACKOFF = 30

# USD per 1M tokens: (input, output). Unknown models report tokens but no cost.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5.5": (2.50, 10.00),
    "deepseek-chat": (0.27, 1.10),
}


class LLMError(RuntimeError):
    """Raised when a request fails and retrying is not going to save us."""


def remaining_time(deadline: float | None, reserve: float = 0.0) -> float:
    """Seconds left before ``deadline`` (a ``time.monotonic`` value), or a large
    number when there is no deadline. Raises ``TimeoutError`` once it has passed."""
    if deadline is None:
        return 3600.0
    left = deadline - time.monotonic() - reserve
    if left <= 0:
        raise TimeoutError("deadline reached")
    return left


@dataclass
class Response:
    """A finished, non-streaming assistant turn."""

    content: str
    tool_calls: list[dict[str, Any]]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning: str = ""


@dataclass
class Usage:
    """Token totals for one session, plus a rough cost estimate."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.requests += 1
        self.input_tokens += prompt_tokens
        self.output_tokens += completion_tokens

    def cost(self, model: str) -> float | None:
        """Estimated USD spend, or None if the model's price is unknown."""
        price = PRICING.get(model)
        if price is None:
            return None
        return self.input_tokens / 1e6 * price[0] + self.output_tokens / 1e6 * price[1]


def session_cost(usage_by_model: dict[str, dict]) -> tuple[float, bool]:
    """Return priced spend and whether every recorded call has a known price."""
    total = 0.0
    complete = True
    for key, counts in usage_by_model.items():
        price = Usage(**counts).cost(key.rsplit("/", 1)[-1]) if key != "unknown" else None
        if price is None:
            complete = False
        else:
            total += price
    return total, complete


class LLM:
    """A thin, retrying wrapper around the chat completions API."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = OpenAI(api_key=config.api_key, base_url=config.base_url)
        self.usage = Usage()

    @property
    def model(self) -> str:
        return self.config.model

    # -- streaming (the agent loop) ---------------------------------------

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        deadline: float | None = None,
    ) -> Iterator[tuple]:
        """Stream one assistant turn, retrying transient failures.

        Yields ``("delta", text)`` and ``("reasoning", text)`` while the model is
        talking, then a single ``("final", assistant_message, usage)`` event.

        On a retryable failure the whole request is re-issued, so text deltas may
        repeat. Only the "final" event is authoritative; the loop rebuilds state
        from it rather than from the deltas.
        """
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                yield from self._stream_once(messages, tools, deadline)
                return
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                last_error = exc
            except APIStatusError as exc:
                if exc.status_code in RETRYABLE_STATUS:
                    last_error = exc
                else:
                    raise LLMError(f"API error {exc.status_code}: {exc}") from exc
            except TimeoutError:
                raise
            if attempt < self.config.max_retries:
                time.sleep(min(2**attempt, MAX_BACKOFF))
        raise LLMError(f"request failed after {self.config.max_retries + 1} attempts: {last_error}")

    def _stream_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        deadline: float | None,
    ) -> Iterator[tuple]:
        remaining_time(deadline)
        kwargs = self._base_kwargs(messages, tools)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        try:
            stream = self.client.chat.completions.create(**kwargs)
        except TypeError:
            # A server that does not understand stream_options. Drop it and retry.
            kwargs.pop("stream_options", None)
            stream = self.client.chat.completions.create(**kwargs)

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}
        usage = None

        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if getattr(delta, "reasoning_content", None):
                reasoning_parts.append(delta.reasoning_content)
                yield ("reasoning", delta.reasoning_content)
            if delta.content:
                text_parts.append(delta.content)
                yield ("delta", delta.content)

            for call in delta.tool_calls or []:
                slot = tool_calls.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
                if call.id:
                    slot["id"] = call.id
                if call.function and call.function.name:
                    slot["name"] = call.function.name
                if call.function and call.function.arguments:
                    slot["arguments"] += call.function.arguments

        message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
        if reasoning_parts:
            message["reasoning"] = "".join(reasoning_parts)
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": slot["id"],
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["arguments"]},
                }
                for _, slot in sorted(tool_calls.items())
            ]
        if usage:
            self.usage.add(getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0)
        yield ("final", message, usage)

    # -- one-shot (browser text helper, utilities) ------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        deadline: float | None = None,
    ) -> Response:
        """A single non-streaming completion."""
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                remaining_time(deadline)
                completion = self.client.chat.completions.create(**self._base_kwargs(messages, tools))
                return self._to_response(completion)
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                last_error = exc
            except APIStatusError as exc:
                if exc.status_code in RETRYABLE_STATUS:
                    last_error = exc
                else:
                    raise LLMError(f"API error {exc.status_code}: {exc}") from exc
            if attempt < self.config.max_retries:
                time.sleep(min(2**attempt, MAX_BACKOFF))
        raise LLMError(f"request failed after {self.config.max_retries + 1} attempts: {last_error}")

    # -- internals ---------------------------------------------------------

    def _base_kwargs(self, messages, tools) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self.config.base_url and urlsplit(self.config.base_url).hostname == "api.deepseek.com":
            kwargs["extra_body"] = {"thinking": {"type": "enabled" if self.config.thinking_enabled else "disabled"}}
        return kwargs

    def _to_response(self, completion) -> Response:
        choice = completion.choices[0].message
        tool_calls = []
        for call in choice.tool_calls or []:
            tool_calls.append(
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
            )
        usage = getattr(completion, "usage", None)
        self.usage.add(getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0)
        return Response(
            content=choice.content or "",
            tool_calls=tool_calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            reasoning=getattr(choice, "reasoning_content", "") or "",
        )
