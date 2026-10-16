"""Sessions as an append-only event log.

The durable truth for a conversation is a JSONL file, one event per line:

    {"v":1, "seq":1, "ts":"...", "type":"start",      "model":..., "workspace":...}
    {"v":1, "seq":2, "ts":"...", "type":"message",    "message":{...}}
    {"v":1, "seq":3, "ts":"...", "type":"checkpoint", "messages":[...]}
    {"v":1, "seq":4, "ts":"...", "type":"plan",       "plan":{...}}

The message list the agent works with is a *projection* replayed from those
events. When context compression shrinks the conversation we append a
``checkpoint`` carrying the new list — the older message events stay in the log,
so the audit trail is never destroyed. Replay starts from the last checkpoint.

This gives resume (replay), fork (copy events into a new id) and share (a
redacted transcript) a single source to work from.
"""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .projection import project_events
from .share import redact
from .store import SessionStore

SCHEMA_VERSION = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _new_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


class Session:
    """One conversation, projected from its event log."""

    def __init__(
        self,
        id: str,
        model: str,
        workspace: str,
        *,
        messages: list[dict] | None = None,
        plan_snapshot: dict | None = None,
        created: str | None = None,
        updated: str | None = None,
        store: SessionStore | None = None,
        seq: int = 0,
        pending_review: dict | None = None,
        incomplete_calls: dict[str, dict] | None = None,
        finished_calls: dict[str, dict] | None = None,
        last_status: str = "complete",
        turn_requests: int = 0,
        turn_tool_rounds: int = 0,
        attachments: list[dict] | None = None,
        background_tasks: dict[str, dict] | None = None,
        subagents: dict[str, dict] | None = None,
        usage: dict | None = None,
        active_mcp_tools: list[str] | None = None,
        active_task_hash: str | None = None,
        task_candidate: str | None = None,
        task_candidate_index: int = 0,
        task_start_index: int = 0,
        task_boundary_pending: bool = False,
        processes: dict[str, dict] | None = None,
        skill_snapshot: list[dict] | None = None,
        benchmark_state: dict | None = None,
    ) -> None:
        self.id = id
        self.model = model
        self.workspace = workspace
        self.messages: list[dict] = list(messages or [])
        self.plan_snapshot = plan_snapshot
        self.created = created or _now()
        self.updated = updated or self.created
        self.store = store or SessionStore()
        self._seq = seq
        self.pending_review = pending_review
        self.incomplete_calls = dict(incomplete_calls or {})
        self.finished_calls = dict(finished_calls or {})
        self.last_status = last_status
        self.turn_requests = turn_requests
        self.turn_tool_rounds = turn_tool_rounds
        self.attachments = list(attachments or [])
        self.background_tasks = dict(background_tasks or {})
        self.subagents = dict(subagents or {})
        self.usage = dict(usage or {"requests": 0, "input_tokens": 0, "output_tokens": 0})
        self.active_mcp_tools = list(active_mcp_tools or [])
        self.active_task_hash = active_task_hash
        self.task_candidate = task_candidate
        self.task_candidate_index = task_candidate_index
        self.task_start_index = task_start_index
        self.task_boundary_pending = task_boundary_pending
        self.processes = dict(processes or {})
        self.skill_snapshot = None if skill_snapshot is None else list(skill_snapshot)
        self.benchmark_state = dict(benchmark_state or {})

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

        state = project_events(events)
        return cls(session_id, state.pop("model"), state.pop("workspace"), store=store, **state)

    @classmethod
    def list_all(cls, store: SessionStore | None = None) -> list[Session]:
        store = store or SessionStore()
        sessions = []
        for session_id in store.ids():
            try:
                sessions.append(cls.load(session_id, store))
            except (ValueError, KeyError, OSError):
                continue  # skip anything unreadable rather than crashing
        return sorted(sessions, key=lambda session: session.updated, reverse=True)

    # -- recording ---------------------------------------------------------

    def record(self, message: dict) -> None:
        """Append a message to the projection and to the log."""
        self._append({"type": "message", "message": message})
        self.messages.append(message)

    def checkpoint(self, messages: list[dict]) -> None:
        """Record a new projection (after compression) without erasing history."""
        self._append({"type": "checkpoint", "messages": list(messages)})
        self.messages = list(messages)
        self.task_start_index = 0
        self.task_boundary_pending = False

    def acknowledge_task_boundary(self) -> None:
        """Record that a task boundary needed no context rewrite."""
        if self.task_boundary_pending:
            self._append({"type": "task_boundary_acknowledged"})
            self.task_boundary_pending = False

    def observe_task(self, decision: str, basis_index: int) -> dict:
        if decision not in ("same", "new", "uncertain"):
            raise ValueError("invalid task boundary decision")
        if basis_index >= len(self.messages) or self.messages[basis_index].get("role") != "user":
            raise ValueError("task boundary must reference a user message")
        candidate = "task_" + hashlib.sha256(f"{self.id}:{self._seq}:{basis_index}".encode()).hexdigest()[:16]
        active_hash = self.active_task_hash
        candidate_hash = self.task_candidate
        candidate_index = self.task_candidate_index
        start_index = self.task_start_index
        confirmed = False
        if active_hash is None:
            active_hash = candidate
            start_index = basis_index
        elif decision == "new":
            candidate_hash = candidate
            candidate_index = basis_index
        elif decision == "same" and candidate_hash:
            active_hash = candidate_hash
            candidate_hash = None
            start_index = candidate_index
            candidate_index = 0
            confirmed = True
        elif decision == "uncertain":
            candidate_hash = None
            candidate_index = 0
        event = {
            "type": "task_boundary_observed", "decision": decision,
            "basis_index": basis_index, "active_hash": active_hash,
            "candidate_hash": candidate_hash, "task_start_index": start_index,
            "candidate_index": candidate_index,
            "confirmed_change": confirmed,
        }
        self._append(event)
        self.active_task_hash = active_hash
        self.task_candidate = candidate_hash
        self.task_candidate_index = candidate_index
        self.task_start_index = start_index
        self.task_boundary_pending = self.task_boundary_pending or confirmed
        return event

    def record_plan(self, snapshot: dict) -> None:
        """Persist the task plan after a successful plan-tool mutation."""
        self._append({"type": "plan", "plan": snapshot})
        self.plan_snapshot = snapshot

    def record_skill_catalog(self, entries: list[dict]) -> None:
        self._append({"type": "skills_snapshotted", "entries": entries})
        self.skill_snapshot = list(entries)

    def set_workspace(self, workspace: str) -> None:
        self._append({"type": "workspace_changed", "workspace": workspace})
        self.workspace = workspace

    def set_model(self, model: str) -> None:
        self._append({"type": "model_changed", "model": model})
        self.model = model

    def record_usage(self, input_tokens: int, output_tokens: int) -> None:
        self._append({"type": "usage", "input_tokens": input_tokens, "output_tokens": output_tokens})
        self.usage["requests"] += 1
        self.usage["input_tokens"] += input_tokens
        self.usage["output_tokens"] += output_tokens

    def record_benchmark_state(self, state: dict) -> None:
        self._append({"type": "benchmark_state", "state": state})
        self.benchmark_state = dict(state)

    def request_review(self, review: dict) -> None:
        self._append({"type": "review_requested", "review": review})
        self.pending_review = review

    def resolve_review(self, request_id: str, decision: str) -> None:
        if self.pending_review is None or self.pending_review.get("id") != request_id:
            raise ValueError("review request is no longer pending")
        self._append({"type": "review_resolved", "request_id": request_id, "decision": decision})
        self.pending_review = None

    def start_tool(self, call: dict) -> None:
        self._append({"type": "tool_started", "call": call})
        self.incomplete_calls[call["id"]] = call

    def finish_tool(self, call_id: str, status: str, content: str) -> None:
        event = {"type": "tool_finished", "call_id": call_id, "status": status, "content": content}
        self._append(event)
        self.incomplete_calls.pop(call_id, None)
        self.finished_calls[call_id] = event

    def set_status(self, status: str) -> None:
        self._append({"type": "turn_status", "status": status})
        self.last_status = status

    def start_turn(self) -> None:
        self._append({"type": "turn_started"})
        self.turn_requests = 0
        self.turn_tool_rounds = 0
        self.active_mcp_tools = []
        self.set_status("running")

    def activate_mcp_tools(self, names: list[str]) -> None:
        fresh = [name for name in names if name not in self.active_mcp_tools]
        if fresh:
            self._append({"type": "mcp_tools_activated", "names": fresh})
            self.active_mcp_tools.extend(fresh)

    def record_model_request(self) -> None:
        self._append({"type": "model_request"})
        self.turn_requests += 1

    def record_tool_round(self) -> None:
        self._append({"type": "tool_round"})
        self.turn_tool_rounds += 1

    def archive_text(self, content: str) -> str:
        digest = self.store.put_blob(self.id, content.encode("utf-8"))
        self._append({"type": "archive", "digest": digest, "chars": len(content)})
        return digest

    def read_archive(self, digest: str) -> str:
        return self.store.read_blob(self.id, digest).decode("utf-8")

    def attach_file(self, path: str | Path, *, max_bytes: int = 10_000_000) -> dict:
        path = Path(path).expanduser().resolve()
        if not path.is_file() or path.stat().st_size > max_bytes:
            raise ValueError("attachment must be a file under 10 MB")
        data = path.read_bytes()
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return self.attach_bytes(path.name, data, media_type, max_bytes=max_bytes)

    def attach_bytes(self, name: str, data: bytes, media_type: str, *, max_bytes: int = 10_000_000) -> dict:
        if len(data) > max_bytes:
            raise ValueError("attachment must be a file under 10 MB")
        if not (media_type.startswith(("text/", "image/"))
                or media_type in ("application/json", "application/xml")):
            raise ValueError("only text and image attachments are supported")
        digest = self.store.put_blob(self.id, data)
        attachment = {
            "name": name,
            "digest": digest,
            "media_type": media_type,
            "size": len(data),
        }
        self._append({"type": "attachment", "attachment": attachment})
        self.attachments.append(attachment)
        return attachment

    def start_background(self, task: dict) -> None:
        self._append({"type": "background_started", "task": task})
        self.background_tasks[task["id"]] = dict(task)

    def finish_background(self, task_id: str, status: str, result: str) -> None:
        task = self.background_tasks[task_id]
        if task["status"] != "running":
            return
        self._append({"type": "background_finished", "task_id": task_id, "status": status, "result": result})
        task.update(status=status, result=result)

    def notify_background(self, task_id: str, message: str | None = None) -> None:
        task = self.background_tasks[task_id]
        if task.get("notified"):
            return
        self._append({"type": "background_notified", "task_id": task_id, "message": message})
        task["notified"] = True
        if message:
            self.messages.append({"role": "user", "content": message, "synthetic": True})

    def record_subagent(self, child: dict) -> None:
        self._append({"type": "subagent_created", "child": child})
        self.subagents[child["id"]] = dict(child)

    def record_process(self, process: dict) -> None:
        self._append({"type": "process_started", "process": process})
        self.processes[process["id"]] = dict(process)

    def finish_process(self, process_id: str, status: str) -> None:
        self._append({"type": "process_finished", "process_id": process_id, "status": status})
        self.processes[process_id]["status"] = status

    def mark_subagent_applied(self, child_id: str) -> None:
        self._append({"type": "subagent_applied", "child_id": child_id})
        self.subagents[child_id]["applied"] = True

    def save(self):
        """Stamp the session as touched. The log is already durable."""
        self._append({"type": "touch"})
        return self.store.path(self.id)

    def _append(self, event: dict) -> None:
        envelope = {"v": SCHEMA_VERSION, "seq": self._seq + 1, "ts": _now(), **event}
        self.store.append(self.id, envelope)
        self._seq += 1
        self.updated = envelope["ts"]

    # -- derived -----------------------------------------------------------

    @property
    def turns(self) -> int:
        return sum(1 for message in self.messages if message.get("role") == "user" and not message.get("synthetic"))

    def fork(self, store: SessionStore | None = None) -> Session:
        """A new session carrying a copy of this conversation so far."""
        clone = Session.new(self.model, self.workspace, store or self.store)
        assets = self.store.root / f"{self.id}.assets"
        if assets.is_dir():
            for path in assets.iterdir():
                if path.is_file() and len(path.name) == 64:
                    copied = clone.store.put_blob(clone.id, self.store.read_blob(self.id, path.name))
                    if copied != path.name:
                        raise ValueError(f"corrupt session asset {path.name}")
        for event in self.store.events(self.id):
            if event.get("type") == "archive":
                clone._append({"type": "archive", "digest": event["digest"], "chars": event["chars"]})
        for attachment in self.attachments:
            clone._append({"type": "attachment", "attachment": attachment})
            clone.attachments.append(attachment)
        for message in self.messages:
            clone.record(message)
        if self.plan_snapshot is not None:
            clone.record_plan(self.plan_snapshot)
        if self.skill_snapshot is not None:
            clone.record_skill_catalog(self.skill_snapshot)
        return clone

    def transcript(self) -> str:
        """A redacted, shareable plain-text transcript."""
        lines = [f"session {self.id}  model {self.model}  {self.created}", ""]
        for message in self.messages:
            role = message.get("role", "?")
            if role == "tool":
                continue
            content = redact(str(message.get("content") or ""))
            if content:
                lines.append(f"[{role}] {content}")
        return "\n".join(lines)
