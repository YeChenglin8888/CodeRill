"""Multi-layer context compression.

A long session eventually exceeds what the model will accept. Rather than fail,
we shrink the conversation in three escalating layers, each only running if the
cheaper one was not enough:

  1. snip      -- trim the bodies of old tool results (cheap, keeps the shape)
  2. summarize -- replace old turns with a model-written summary
  3. collapse  -- last resort: keep only the summary and the most recent turns

``maybe_compress`` returns True if it changed anything.
"""

from __future__ import annotations

from .llm import LLM

CHARS_PER_TOKEN = 4
KEEP_RECENT = 6       # messages never summarized
SNIP_KEEP = 200       # characters kept from a snipped tool result
SUMMARY_PROMPT = (
    "Summarize the conversation so far for your own future reference. Keep goals, "
    "decisions, file paths, exact identifiers, and anything still unresolved. Be "
    "concise and factual; drop pleasantries."
)


def estimate_tokens(messages: list[dict]) -> int:
    """A rough token count: about four characters per token."""
    total = 0
    for message in messages:
        total += len(message.get("content") or "") // CHARS_PER_TOKEN + 4
        for call in message.get("tool_calls") or []:
            total += len(call.get("function", {}).get("arguments", "")) // CHARS_PER_TOKEN
    return total


class ContextManager:
    """Shrinks a message list in place when it grows past a token budget."""

    def __init__(self, max_tokens: int = 128_000, *, threshold: float = 0.8) -> None:
        self.max_tokens = max_tokens
        self.threshold = threshold
        self.summary = ""

    @property
    def budget(self) -> int:
        return int(self.max_tokens * self.threshold)

    def maybe_compress(self, messages: list[dict], llm: LLM | None = None) -> bool:
        if estimate_tokens(messages) <= self.budget:
            return False

        changed = self._snip(messages)
        if estimate_tokens(messages) <= self.budget:
            return changed

        changed = self._summarize(messages, llm) or changed
        if estimate_tokens(messages) <= self.budget:
            return changed

        return self._collapse(messages, llm) or changed

    # -- layers ------------------------------------------------------------

    def _snip(self, messages: list[dict]) -> bool:
        """Trim the bodies of tool results that are no longer recent."""
        cutoff = len(messages) - KEEP_RECENT
        changed = False
        for index, message in enumerate(messages):
            if index >= cutoff or message.get("role") != "tool":
                continue
            content = message.get("content") or ""
            if len(content) > SNIP_KEEP:
                message["content"] = content[:SNIP_KEEP] + "\n...[snipped to save context]"
                changed = True
        return changed

    def _summarize(self, messages: list[dict], llm: LLM | None) -> bool:
        """Replace everything before the recent window with a written summary."""
        index = _safe_split(messages, KEEP_RECENT)
        if index <= 0:
            return False

        note = (self.summary + "\n\n" + _flatten(messages[:index])).strip()
        self.summary = self._write_summary(note, llm)
        messages[:] = [_summary_message(self.summary), *messages[index:]]
        return True

    def _collapse(self, messages: list[dict], llm: LLM | None) -> bool:
        """Keep only the summary and the last couple of turns."""
        if not self.summary:
            self._summarize(messages, llm)
        index = _safe_split(messages, 2)
        messages[:] = [_summary_message(self.summary), *messages[index:]]
        return True

    def _write_summary(self, note: str, llm: LLM | None) -> str:
        if llm is None:
            return _extract(note)
        try:
            response = llm.chat(
                [
                    {"role": "system", "content": SUMMARY_PROMPT},
                    {"role": "user", "content": note[:60_000]},
                ]
            )
            return response.content.strip() or _extract(note)
        except Exception:  # noqa: BLE001 - a failed summary must not break the loop
            return _extract(note)


def _safe_split(messages: list[dict], keep_recent: int) -> int:
    """Index where the recent window starts, never splitting a tool call from
    its result (which the API rejects)."""
    index = max(len(messages) - keep_recent, 0)
    while index > 0 and messages[index].get("role") == "tool":
        index -= 1
    return index


def _summary_message(summary: str) -> dict:
    return {"role": "user", "content": f"[Summary of earlier conversation]\n{summary}"}


def _flatten(messages: list[dict]) -> str:
    lines = []
    for message in messages:
        role = message.get("role", "?")
        text = message.get("content") or ""
        if message.get("tool_calls"):
            names = [c.get("function", {}).get("name", "?") for c in message["tool_calls"]]
            text = f"{text} (called: {', '.join(names)})"
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


def _extract(note: str) -> str:
    """A non-LLM fallback summary: keep the head of the transcript."""
    return note[:2000] + ("..." if len(note) > 2000 else "")
