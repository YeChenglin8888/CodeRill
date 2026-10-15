"""Translate internal messages to vendor requests at the provider boundary."""

from __future__ import annotations

import base64
import json
from typing import Any

from .types import ChatMessage, ChatRequest, ToolCall


def parse_arguments(raw: str | dict[str, Any] | None) -> dict[str, Any] | str:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return str(raw)
    return value if isinstance(value, dict) else str(raw)


def openai_messages(request: ChatRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for message in request.messages:
        item: dict[str, Any] = {"role": message.role, "content": _openai_content(message)}
        if message.tool_call_id:
            item["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": _arguments_json(call)},
                }
                for call in message.tool_calls
            ]
        messages.append(item)
    return messages


def _openai_content(message: ChatMessage) -> str | list[dict[str, Any]] | None:
    if not message.parts:
        return message.content or (None if message.tool_calls else "")
    blocks: list[dict[str, Any]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    for part in message.parts:
        if part.kind == "text":
            blocks.append({"type": "text", "text": part.text})
        else:
            encoded = base64.b64encode(part.data).decode("ascii")
            blocks.append({"type": "image_url", "image_url": {"url": f"data:{part.media_type};base64,{encoded}"}})
    return blocks


def anthropic_messages(request: ChatRequest) -> tuple[str, list[dict[str, Any]]]:
    system: list[str] = []
    messages: list[dict[str, Any]] = []
    pending_results: list[dict[str, Any]] = []
    for message in request.messages:
        if message.role == "system":
            system.append(message.content)
            continue
        if message.role == "tool":
            pending_results.append(
                {"type": "tool_result", "tool_use_id": message.tool_call_id, "content": message.content}
            )
            continue
        if pending_results:
            messages.append({"role": "user", "content": pending_results})
            pending_results = []
        if message.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": parse_arguments(call.arguments)}
                for call in message.tool_calls
            )
            messages.append({"role": "assistant", "content": blocks or message.content})
        else:
            messages.append({"role": "user", "content": _anthropic_content(message)})
    if pending_results:
        messages.append({"role": "user", "content": pending_results})
    return "\n\n".join(text for text in system if text), messages


def _anthropic_content(message: ChatMessage) -> str | list[dict[str, Any]]:
    if not message.parts:
        return message.content
    blocks: list[dict[str, Any]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    for part in message.parts:
        if part.kind == "text":
            blocks.append({"type": "text", "text": part.text})
        else:
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": part.media_type,
                    "data": base64.b64encode(part.data).decode("ascii"),
                },
            })
    return blocks


def _arguments_json(call: ToolCall) -> str:
    return call.arguments if isinstance(call.arguments, str) else json.dumps(call.arguments, ensure_ascii=False)


def read_field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)
