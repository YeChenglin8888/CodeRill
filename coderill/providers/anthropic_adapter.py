"""Native Anthropic Messages adapter for the shared chat contract."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

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
from .wire import anthropic_messages, parse_arguments, read_field


class AnthropicChatProvider:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        vision: bool = True,
        attempts: int = 3,
        client: Any = None,
    ) -> None:
        self.name = "anthropic"
        self.model = model
        self.capabilities = ProviderCapabilities(vision=vision)
        self.attempts = attempts
        if client is None:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=api_key, base_url=base_url)
        self.client = client

    def _params(self, request: ChatRequest) -> dict[str, Any]:
        system, messages = anthropic_messages(request)
        params: dict[str, Any] = {"model": self.model, "max_tokens": request.max_tokens, "messages": messages}
        if system:
            params["system"] = system
        if request.tools:
            params["tools"] = [
                {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}
                for tool in request.tools
            ]
        return params

    async def complete(self, request: ChatRequest) -> ChatResponse:
        params = self._params(request)

        async def call():
            try:
                return await self.client.messages.create(**params)
            except Exception as exc:
                raise classify_error(exc) from exc

        raw = await retry_async(call, attempts=self.attempts, deadline=request.deadline)
        blocks = read_field(raw, "content", []) or []
        calls = tuple(
            ToolCall(read_field(block, "id") or "", read_field(block, "name") or "", read_field(block, "input") or {})
            for block in blocks
            if read_field(block, "type") == "tool_use"
        )
        finish = _finish(read_field(raw, "stop_reason"), calls)
        return ChatResponse(
            content="".join(read_field(block, "text") or "" for block in blocks if read_field(block, "type") == "text"),
            tool_calls=() if finish == "length" else calls,
            finish_reason=finish,
            reasoning="".join(read_field(block, "thinking") or "" for block in blocks),
            usage=_usage(read_field(raw, "usage")),
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        params = self._params(request)
        params["stream"] = True

        async def start():
            try:
                return await self.client.messages.create(**params)
            except Exception as exc:
                raise classify_error(exc) from exc

        stream = await retry_async(start, attempts=self.attempts, deadline=request.deadline)
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage = TokenUsage()
        stop_reason: str | None = None
        try:
            async for event in stream:
                kind = read_field(event, "type")
                if kind == "message_start":
                    usage = _usage(read_field(read_field(event, "message"), "usage"))
                elif kind == "content_block_start":
                    block = read_field(event, "content_block")
                    if read_field(block, "type") == "tool_use":
                        calls[read_field(event, "index")] = {
                            "id": read_field(block, "id") or "",
                            "name": read_field(block, "name") or "",
                            "arguments": "",
                            "input": read_field(block, "input") or {},
                        }
                    elif read_field(block, "type") == "text" and read_field(block, "text"):
                        text = read_field(block, "text")
                        content.append(text)
                        yield ChatStreamEvent("text_delta", text=text)
                elif kind == "content_block_delta":
                    delta = read_field(event, "delta")
                    delta_type = read_field(delta, "type")
                    if delta_type == "text_delta":
                        text = read_field(delta, "text") or ""
                        content.append(text)
                        yield ChatStreamEvent("text_delta", text=text)
                    elif delta_type == "thinking_delta":
                        text = read_field(delta, "thinking") or ""
                        reasoning.append(text)
                        yield ChatStreamEvent("reasoning_delta", text=text)
                    elif delta_type == "input_json_delta":
                        fragment = read_field(delta, "partial_json") or ""
                        calls[read_field(event, "index")]["arguments"] += fragment
                        yield ChatStreamEvent("tool_call_delta", text=fragment)
                elif kind == "message_delta":
                    delta = read_field(event, "delta")
                    stop_reason = read_field(delta, "stop_reason") or stop_reason
                    new_usage = read_field(event, "usage")
                    if new_usage is not None:
                        usage = TokenUsage(usage.input_tokens, read_field(new_usage, "output_tokens", 0) or 0)
        except Exception as exc:
            raise classify_error(exc) from exc
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                await close()

        tool_calls = tuple(
            ToolCall(slot["id"], slot["name"], parse_arguments(slot["arguments"]) if slot["arguments"] else slot["input"])
            for _, slot in sorted(calls.items())
        )
        finish = _finish(stop_reason, tool_calls)
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


def _usage(raw: Any) -> TokenUsage:
    return TokenUsage(read_field(raw, "input_tokens", 0) or 0, read_field(raw, "output_tokens", 0) or 0)


def _finish(raw: str | None, calls: tuple[ToolCall, ...]) -> str:
    if raw == "max_tokens":
        return "length"
    return "tool_calls" if calls else "stop"
