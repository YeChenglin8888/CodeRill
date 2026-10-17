"""Four bounded reductions over a provider-visible session view."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from ..session import Session
from .content import semantic_preview


class CompactionPipeline:
    def __init__(self, session: Session, messages: list[dict]) -> None:
        self.session = session
        self.messages = messages

    def l1_old_dialog(self, cutoff: int) -> None:
        for message in self.messages[:cutoff]:
            if message.get("role") not in ("user", "assistant") or message.get("tool_calls") or message.get("reasoning"):
                continue
            content = message.get("content") or ""
            if len(content) > 1200:
                self._replace(message, content[:400] + "\n[older dialogue trimmed]", content)

    def l2_typed_outputs(self, old_limit: int, latest_failure: str | None) -> None:
        for message in self.messages[:old_limit]:
            if message.get("role") != "tool" or message.get("tool_call_id") == latest_failure:
                continue
            content = message.get("content") or ""
            if len(content) <= 800 or "[archive:" in content:
                continue
            preview, kind = semantic_preview(content)
            self._replace(message, f"[{kind} preview]\n{preview}", content)

    def l3_archive_remaining(self, old_limit: int, latest_failure: str | None) -> None:
        for message in self.messages[:old_limit]:
            if message.get("role") != "tool" or message.get("tool_call_id") == latest_failure:
                continue
            content = message.get("content") or ""
            if len(content) > 800 and "[archive:" not in content:
                self._replace(message, content[:200] + "\n...\n" + content[-100:], content)

    async def l4_checkpoint(self, cutoff: int, summarize: Callable[[str], Awaitable[str]]) -> None:
        if cutoff <= 0:
            return
        original = json.dumps(self.session.messages[:cutoff], ensure_ascii=False)
        digest = self.session.archive_text(original)
        summary = await summarize(_index(self.messages[:cutoff]))
        self.messages = [{"role": "user", "content": (
            f"[Earlier conversation checkpoint; full messages archive:{digest}]\n{summary[:1000]}"
        )}, *self.messages[cutoff:]]

    def _replace(self, message: dict, preview: str, original: str) -> None:
        if len(preview) + 110 >= len(original):
            return
        digest = self.session.archive_text(original)
        message["content"] = f"{preview}\n[archive:{digest}; use archive_read for the full text]"


def _index(messages: list[dict]) -> str:
    lines = []
    for message in messages:
        role = message.get("role", "?")
        content = str(message.get("content") or "")[:350]
        calls = ", ".join(call.get("function", {}).get("name", "?") for call in message.get("tool_calls", []))
        lines.append(f"{role}: {content}" + (f" [called {calls}]" if calls else ""))
    return "\n".join(lines)
