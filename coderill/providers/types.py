"""Provider-neutral messages and stream events used by the runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]
FinishReason = Literal["stop", "tool_calls", "length", "error"]
StreamKind = Literal[
    "text_delta", "reasoning_delta", "tool_call_delta", "completed",
    "tool_started", "tool_result", "review_requested", "status",
]


@dataclass(frozen=True)
class ContentPart:
    kind: Literal["text", "image"]
    text: str = ""
    media_type: str = ""
    data: bytes = b""


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | str


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    status: Literal["success", "denied", "failed", "interrupted", "unknown"]
    content: str


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str = ""
    parts: tuple[ContentPart, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""


@dataclass(frozen=True)
class ChatRequest:
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()
    max_tokens: int = 4096
    temperature: float | None = None
    deadline: float | None = None


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ChatResponse:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: FinishReason = "stop"
    reasoning: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True)
class ChatStreamEvent:
    kind: StreamKind
    text: str = ""
    tool_call: ToolCall | None = None
    response: ChatResponse | None = None
    review: dict[str, Any] | None = None
    status: str = ""


@dataclass(frozen=True)
class ProviderCapabilities:
    vision: bool = False
    streaming: bool = True
