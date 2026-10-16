"""Budget a request and keep compressed text recoverable from session assets."""

from __future__ import annotations

import json
from pathlib import Path

from ..providers.base import ChatProvider
from ..providers.types import ChatMessage, ChatRequest, ToolDefinition
from ..session import Session
from .content import semantic_preview
from .pipeline import CompactionPipeline


class ContextBudgetExceeded(RuntimeError):
    pass


def estimate_request_tokens(system: str, tools: tuple[ToolDefinition, ...], messages: list[dict], output: int) -> int:
    chars = len(system) + len(json.dumps([tool.__dict__ for tool in tools], ensure_ascii=False))
    chars += len(json.dumps(messages, ensure_ascii=False))
    for message in messages:
        for part in message.get("attachments", []):
            chars += int(part.get("size", 0)) * 2
    return chars // 4 + 16 * (len(messages) + len(tools)) + output


class AsyncContextManager:
    def __init__(self, max_tokens: int, *, reserve: int) -> None:
        self.max_tokens = max_tokens
        self.reserve = reserve

    async def prepare(
        self,
        session: Session,
        system: str,
        tools: tuple[ToolDefinition, ...],
        provider: ChatProvider,
        *,
        force: bool = False,
    ) -> list[dict]:
        messages = [dict(message) for message in session.messages]
        _mark_stale_reads(messages, session)
        marked_messages = [dict(message) for message in messages]
        before = estimate_request_tokens(system, tools, messages, self.reserve)
        if before <= self.max_tokens and not force:
            return messages
        budget = int(self.max_tokens * (0.65 if force else 0.85))
        cutoff = session.task_start_index if session.task_boundary_pending else _old_turn_boundary(messages)
        old_limit = max(len(messages) - 8, 0)
        latest_failure = next((message.get("tool_call_id") for message in reversed(messages)
                               if message.get("role") == "tool" and session.finished_calls.get(
                                   message.get("tool_call_id", ""), {}).get("status") == "failed"), None)
        pipeline = CompactionPipeline(session, messages)
        pipeline.l1_old_dialog(cutoff)
        if estimate_request_tokens(system, tools, messages, self.reserve) > budget:
            pipeline.l2_typed_outputs(old_limit, latest_failure)
        if estimate_request_tokens(system, tools, messages, self.reserve) > budget:
            pipeline.l3_archive_remaining(old_limit, latest_failure)
        if cutoff and (force or estimate_request_tokens(system, tools, messages, self.reserve) > budget):
            await pipeline.l4_checkpoint(cutoff, lambda note: self._summarize(provider, note))
        messages = pipeline.messages

        after = estimate_request_tokens(system, tools, messages, self.reserve)
        if after >= before and before <= self.max_tokens:
            if force and session.task_boundary_pending:
                session.acknowledge_task_boundary()
            return marked_messages
        if after >= before or after > self.max_tokens:
            raise ContextBudgetExceeded(f"request needs about {after} tokens; context limit is {self.max_tokens}")
        session.checkpoint(messages)
        return messages

    async def _summarize(self, provider: ChatProvider, note: str) -> str:
        try:
            response = await provider.complete(ChatRequest((
                ChatMessage("system", "Summarize goals, decisions, paths, exact identifiers and unresolved work."),
                ChatMessage("user", note[:30_000]),
            ), max_tokens=min(1024, self.reserve)))
            if response.content.strip():
                return response.content.strip()
        except Exception:  # noqa: BLE001 - deterministic index remains usable
            return note[:4000]
        return note[:4000]


def _old_turn_boundary(messages: list[dict], keep_recent: int = 8) -> int:
    index = max(len(messages) - keep_recent, 0)
    while index > 0 and messages[index].get("role") != "user":
        index -= 1
    return index


def _mark_stale_reads(messages: list[dict], session: Session) -> None:
    """Keep old reads visible as history without presenting them as current file state."""
    calls: dict[str, tuple[str, dict]] = {}
    reads: dict[Path, list[int]] = {}
    root = Path(session.workspace)
    for index, message in enumerate(messages):
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []):
                function = call.get("function", {})
                try:
                    args = json.loads(function.get("arguments") or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(args, dict):
                    calls[call.get("id", "")] = (function.get("name", ""), args)
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id", "")
            if session.finished_calls.get(call_id, {}).get("status") != "success":
                continue
            name, args = calls.get(call_id, ("", {}))
            if name == "apply_patch":
                for previous, indexes in list(reads.items()):
                    for read_index in indexes:
                        old = messages[read_index]
                        if "[stale read:" not in old.get("content", ""):
                            old["content"] += f"\n[stale read: a patch may have changed {previous.name}; reread before relying on this output]"
                reads.clear()
                continue
            path = args.get("path") if name in ("read_file", "write_file", "delete_path") else args.get("file_path")
            if not isinstance(path, str):
                continue
            target = (root / path).resolve()
            if name == "read_file":
                reads.setdefault(target, []).append(index)
            elif name in ("write_file", "edit_file", "delete_path"):
                for read_index in reads.pop(target, []):
                    old = messages[read_index]
                    if "[stale read:" not in old.get("content", ""):
                        old["content"] += f"\n[stale read: {path} changed later; reread before relying on this output]"


def _semantic_preview(content: str) -> str:
    return semantic_preview(content)[0]
