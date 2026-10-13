"""Sessions as an append-only event log.

The durable truth for a conversation is a JSONL file, one event per line:

    {"v":1, "seq":1, "ts":"...", "type":"start",      "model":..., "workspace":...}
    {"v":1, "seq":2, "ts":"...", "type":"message",    "message":{...}}
    {"v":1, "seq":3, "ts":"...", "type":"checkpoint", "messages":[...]}

The message list the agent works with is a *projection* replayed from those
events. When context compression shrinks the conversation we append a
``checkpoint`` carrying the new list — the older message events stay in the log,
so the audit trail is never destroyed. Replay starts from the last checkpoint.

This gives resume (replay), fork (copy events into a new id) and share (a
redacted transcript) a single source to work from.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

SESSION_DIR = Path.home() / ".coderill" / "sessions"
SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _new_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


class SessionStore:
    """Append-only JSONL storage, one file per session."""

    def __init__(self, root: str | Path | None = None) -> None:
        # Read the module-level default at call time so tests can repoint it.
        self.root = Path(root) if root is not None else SESSION_DIR

    def path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.jsonl"

    def append(self, session_id: str, event: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.path(session_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def events(self, session_id: str) -> list[dict]:
        path = self.path(session_id)
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def ids(self) -> list[str]:
        if not self.root.exists():
            return []
        return [path.stem for path in self.root.glob("*.jsonl")]


class Session:
    """One conversation, projected from its event log."""

    def __init__(
        self,
        id: str,
        model: str,
        workspace: str,
        *,
        messages: list[dict] | None = None,
        created: str | None = None,
        updated: str | None = None,
        store: SessionStore | None = None,
        seq: int = 0,
    ) -> None:
        self.id = id
        self.model = model
        self.workspace = workspace
        self.messages: list[dict] = list(messages or [])
        self.created = created or _now()
        self.updated = updated or self.created
        self.store = store or SessionStore()
        self._seq = seq

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def new(cls, model: str, workspace: str, store: SessionStore | None = None) -> Session:
        session = cls(_new_id(), model, workspace, store=store)
        session._append({"type": "start", "model": model, "workspace": workspace})
        return session

    @classmethod
    def load(cls, session_id: str, store: SessionStore | None = None) -> Session:
        store = store or SessionStore()
        events = store.events(session_id)
        if not events:
            raise FileNotFoundError(f"no session '{session_id}'")

        start = next((event for event in events if event.get("type") == "start"), None)
        if start is None or start.get("v") != SCHEMA_VERSION:
            raise ValueError(f"unsupported or missing session schema for '{session_id}'")

        messages: list[dict] = []
        for event in events:
            if event.get("type") == "checkpoint":
                messages = list(event.get("messages", []))
            elif event.get("type") == "message":
                messages.append(event["message"])

        return cls(
            session_id,
            start.get("model", ""),
            start.get("workspace", ""),
            messages=messages,
            created=start.get("ts"),
            updated=events[-1].get("ts"),
            store=store,
            seq=events[-1].get("seq", 0),
        )

    @classmethod
    def list_all(cls, store: SessionStore | None = None) -> list[Session]:
        store = store or SessionStore()
        sessions = []
        for session_id in store.ids():
            try:
                sessions.append(cls.load(session_id, store))
            except (ValueError, KeyError, OSError, json.JSONDecodeError):
                continue  # skip anything unreadable rather than crashing
        return sorted(sessions, key=lambda session: session.updated, reverse=True)

    # -- recording ---------------------------------------------------------

    def record(self, message: dict) -> None:
        """Append a message to the projection and to the log."""
        self.messages.append(message)
        self._append({"type": "message", "message": message})

    def checkpoint(self, messages: list[dict]) -> None:
        """Record a new projection (after compression) without erasing history."""
        self.messages = list(messages)
        self._append({"type": "checkpoint", "messages": list(messages)})

    def save(self) -> Path:
        """Stamp the session as touched. The log is already durable."""
        self._append({"type": "touch"})
        return self.store.path(self.id)

    def _append(self, event: dict) -> None:
        self._seq += 1
        envelope = {"v": SCHEMA_VERSION, "seq": self._seq, "ts": _now(), **event}
        self.updated = envelope["ts"]
        self.store.append(self.id, envelope)

    # -- derived -----------------------------------------------------------

    @property
    def turns(self) -> int:
        return sum(1 for message in self.messages if message.get("role") == "user")

    def fork(self, store: SessionStore | None = None) -> Session:
        """A new session carrying a copy of this conversation so far."""
        clone = Session.new(self.model, self.workspace, store or self.store)
        for message in self.messages:
            clone.record(message)
        return clone

    def transcript(self) -> str:
        """A redacted, shareable plain-text transcript."""
        lines = [f"session {self.id}  model {self.model}  {self.created}", ""]
        for message in self.messages:
            role = message.get("role", "?")
            if role == "tool":
                continue
            content = _redact(str(message.get("content") or ""))
            if content:
                lines.append(f"[{role}] {content}")
        return "\n".join(lines)


# Obvious credential-looking strings, replaced before a transcript is shared.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*[=:]\s*\S+"),
]


def _redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text
