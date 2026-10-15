"""Budget a request and keep compressed text recoverable from session assets."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..providers.base import ChatProvider
from ..providers.types import ChatMessage, ChatRequest, ToolDefinition
from ..session import Session


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

        for index in range(old_limit):
            message = messages[index]
            if message.get("role") != "tool" or message.get("tool_call_id") == latest_failure:
                continue
            content = message.get("content") or ""
            if len(content) > 800 and "[archive:" not in content:
                digest = session.archive_text(content)
                message["content"] = f"{_semantic_preview(content)}\n[archive:{digest}; use archive_read for the full output]"
        if estimate_request_tokens(system, tools, messages, self.reserve) > budget:
            for index in range(cutoff):
                message = messages[index]
                if message.get("role") not in ("user", "assistant"):
                    continue
                content = message.get("content") or ""
                if len(content) > 1200 and "[archive:" not in content:
                    digest = session.archive_text(content)
                    message["content"] = f"{content[:400]}\n[archive:{digest}; use archive_read for the full text]"

        if cutoff and (force or estimate_request_tokens(system, tools, messages, self.reserve) > budget):
            original = json.dumps(messages[:cutoff], ensure_ascii=False)
            digest = session.archive_text(original)
            index = _index(messages[:cutoff])
            summary = await self._summarize(provider, index)
            messages = [{"role": "user", "content": (
                f"[Earlier conversation checkpoint; full messages archive:{digest}]\n{summary[:1000]}"
            )}, *messages[cutoff:]]

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


def _index(messages: list[dict]) -> str:
    lines = []
    for message in messages:
        role = message.get("role", "?")
        content = str(message.get("content") or "")[:350]
        calls = ", ".join(call.get("function", {}).get("name", "?") for call in message.get("tool_calls", []))
        lines.append(f"{role}: {content}" + (f" [called {calls}]" if calls else ""))
    return "\n".join(lines)


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
            path = args.get("path") if name in ("read_file", "write_file") else args.get("file_path")
            if not isinstance(path, str):
                continue
            target = (root / path).resolve()
            if name == "read_file":
                reads.setdefault(target, []).append(index)
            elif name in ("write_file", "edit_file"):
                for read_index in reads.pop(target, []):
                    old = messages[read_index]
                    if "[stale read:" not in old.get("content", ""):
                        old["content"] += f"\n[stale read: {path} changed later; reread before relying on this output]"


def _semantic_preview(content: str) -> str:
    """Preserve useful structure in an old tool result; the archive holds exact bytes."""
    stripped = content.strip()
    if stripped.startswith(("{", "[")):
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(value, dict):
                examples = [f"{key}={str(item)[:80]}" for key, item in list(value.items())[:5]
                            if isinstance(item, (str, int, float, bool))]
                return f"JSON object with keys: {', '.join(map(str, list(value)[:20]))}\n" + "; ".join(examples)
            if isinstance(value, list):
                return f"JSON array with {len(value)} items; first item: {str(value[0])[:250] if value else '(empty)'}"
    lines = content.splitlines()
    if any(line.startswith(("diff --git ", "@@ ")) for line in lines):
        files = [line for line in lines if line.startswith("diff --git ")][:10]
        added = sum(line.startswith("+") and not line.startswith("+++") for line in lines)
        removed = sum(line.startswith("-") and not line.startswith("---") for line in lines)
        return f"Diff: {added} added, {removed} removed\n" + "\n".join(files)
    matches = [match for line in lines if (match := re.match(r"^(.+?):(\d+):\s*(.*)$", line))]
    if len(matches) >= 3:
        paths: dict[str, list[str]] = {}
        for match in matches:
            paths.setdefault(match[1], []).append(f"{match[1]}:{match[2]}: {match[3]}")
        kept = [f"{path} ({len(hits)} hits):\n" + "\n".join(hits[:2] + hits[-1:])
                for path, hits in list(paths.items())[:5]]
        return f"{len(matches)} search hits in {len(paths)} files:\n" + "\n".join(kept)[:600]
    if re.search(r"<!doctype\s+html|<html\b", content[:1000], re.IGNORECASE):
        title = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
        headings = re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", content, re.IGNORECASE | re.DOTALL)
        def clean(value: str) -> str:
            return re.sub(r"<[^>]+>|\s+", " ", value).strip()
        return "HTML" + (f" title: {clean(title[1])}" if title else "") + \
            ("\nHeadings: " + "; ".join(clean(item) for item in headings[:8]) if headings else "")
    indexes = [index for index, line in enumerate(lines)
               if re.search(r"error|failed|traceback|exception", line, re.IGNORECASE)]
    if indexes:
        selected = sorted({near for index in indexes[-5:]
                           for near in range(max(0, index - 1), min(len(lines), index + 2))})
        return "Relevant errors and nearby lines:\n" + "\n".join(lines[index] for index in selected)[-700:]
    signatures = [(index + 1, line.strip()) for index, line in enumerate(lines)
                  if re.match(r"\s*(?:async\s+def|def|class|function|export\s+function|fn|struct|interface)\b", line)]
    if signatures:
        imports = [line.strip() for line in lines if re.match(r"\s*(?:import|from|use|#include)\b", line)]
        return f"Source code, {len(lines)} lines:\n" + "\n".join(
            [*imports[:4], *(f"L{number}: {line}" for number, line in signatures[:15])])[:700]
    return content[:300] + ("\n...\n" + content[-120:] if len(content) > 500 else "")
