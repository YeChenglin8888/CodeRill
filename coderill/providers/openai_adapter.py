"""Async OpenAI Chat Completions adapter with no SDK objects in its results."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

from .errors import classify_error
from .retry import retry_async
from .types import (
    ChatRequest,
    ChatResponse,
    ChatStreamEvent,
    ProviderCapabilities,
    TokenUsage,
    ToolCall,
)
from .wire import openai_messages, parse_arguments, read_field


class OpenAIChatProvider:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        name: str = "openai",
        vision: bool = False,
        attempts: int = 3,
        client: Any = None,
    ) -> None:
        self.name = name
        self.model = model
        self.deepseek = urlsplit(base_url).hostname == "api.deepseek.com" if base_url else False
        self.capabilities = ProviderCapabilities(vision=vision)
        self.attempts = attempts
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.client = client

    def _params(self, request: ChatRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages(request, include_reasoning=self.deepseek),
            "max_tokens": request.max_tokens,
        }
        if request.temperature is not None:
            params["temperature"] = request.temperature
        if request.tools:
            params["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in request.tools
            ]
            params["tool_choice"] = "auto"
        if self.deepseek:
            params["extra_body"] = {"thinking": {"type": "enabled"}}
        return params

    async def complete(self, request: ChatRequest) -> ChatResponse:
        params = self._params(request)

        async def call():
            try:
                return await self.client.chat.completions.create(**params)
            except Exception as exc:
                raise classify_error(exc) from exc

        raw = await retry_async(call, attempts=self.attempts, deadline=request.deadline)
        choice = read_field(raw, "choices")[0]
        message = read_field(choice, "message")
        calls = tuple(_tool_call(item) for item in read_field(message, "tool_calls", []) or [])
        finish = _finish(read_field(choice, "finish_reason"), calls)
        return ChatResponse(
            content=read_field(message, "content") or "",
            tool_calls=() if finish == "length" else calls,
            finish_reason=finish,
            reasoning=read_field(message, "reasoning_content") or "",
            usage=_usage(read_field(raw, "usage")),
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        params = self._params(request)
        params.update(stream=True, stream_options={"include_usage": True})

        async def start():
            try:
                return await self.client.chat.completions.create(**params)
            except Exception as exc:
                raise classify_error(exc) from exc

        stream = await retry_async(start, attempts=self.attempts, deadline=request.deadline)
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage = TokenUsage()
        finish_reason: str | None = None
        try:
            async for chunk in stream:
                if read_field(chunk, "usage") is not None:
                    usage = _usage(read_field(chunk, "usage"))
                choices = read_field(chunk, "choices", []) or []
                if not choices:
                    continue
                choice = choices[0]
                finish_reason = read_field(choice, "finish_reason") or finish_reason
                delta = read_field(choice, "delta")
                text = read_field(delta, "content") or ""
                if text:
                    content.append(text)
                    yield ChatStreamEvent("text_delta", text=text)
                thought = read_field(delta, "reasoning_content") or ""
                if thought:
                    reasoning.append(thought)
                    yield ChatStreamEvent("reasoning_delta", text=thought)
                for part in read_field(delta, "tool_calls", []) or []:
                    index = read_field(part, "index")
                    slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    slot["id"] = read_field(part, "id") or slot["id"]
                    function = read_field(part, "function")
                    slot["name"] = read_field(function, "name") or slot["name"]
                    fragment = read_field(function, "arguments") or ""
                    if fragment:
                        slot["arguments"] += fragment
                        yield ChatStreamEvent("tool_call_delta", text=fragment)
        except Exception as exc:
            raise classify_error(exc) from exc
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                await close()

        tool_calls = tuple(
            ToolCall(slot["id"], slot["name"], parse_arguments(slot["arguments"]))
            for _, slot in sorted(calls.items())
        )
        finish = _finish(finish_reason, tool_calls)
        yield ChatStreamEvent(
            "completed",
            response=ChatResponse(
                content="".join(content),
                tool_calls=() if finish == "length" else tool_calls,
                finish_reason=finish,
                reasoning="".join(reasoning),
                usage=usage,
            ),
        )


def _tool_call(raw: Any) -> ToolCall:
    function = read_field(raw, "function")
    return ToolCall(
        id=read_field(raw, "id") or "",
        name=read_field(function, "name") or "",
        arguments=parse_arguments(read_field(function, "arguments")),
    )


def _usage(raw: Any) -> TokenUsage:
    return TokenUsage(read_field(raw, "prompt_tokens", 0) or 0, read_field(raw, "completion_tokens", 0) or 0)


def _finish(raw: str | None, calls: tuple[ToolCall, ...]) -> str:
    if raw == "length":
        return "length"
    return "tool_calls" if calls else "stop"
