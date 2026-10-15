"""The model interface consumed by the agent and context layers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from .types import ChatRequest, ChatResponse, ChatStreamEvent, ProviderCapabilities


class ChatProvider(Protocol):
    name: str
    model: str
    capabilities: ProviderCapabilities

    async def complete(self, request: ChatRequest) -> ChatResponse: ...

    def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]: ...
