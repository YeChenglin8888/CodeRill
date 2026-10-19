"""Choose the reply language from the latest human message."""

from __future__ import annotations


def is_chinese(messages: list[dict]) -> bool:
    for message in reversed(messages):
        if message.get("role") == "user" and not message.get("synthetic"):
            content = str(message.get("content") or "")
            return any("\u4e00" <= char <= "\u9fff" for char in content)
    return False
