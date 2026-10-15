"""Tool-call preflight, ordered execution, and result settlement."""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..hooks import Hooks
from ..permissions import Permission
from ..session import Session
from ..taskplan import TaskPlan
from ..tools import Registry

MAX_PARALLEL = 4


class ToolExecutor:
    def __init__(
        self,
        *,
        registry: Registry,
        permissions: Permission,
        hooks: Hooks,
        session: Session,
        plan: TaskPlan,
        on_tool: Callable[[str, dict[str, Any]], None],
        on_tool_result: Callable[[str, str], None],
    ) -> None:
        self.registry = registry
        self.permissions = permissions
        self.hooks = hooks
        self.session = session
        self.plan = plan
        self.on_tool = on_tool
        self.on_tool_result = on_tool_result

    def run(self, tool_calls: list[dict[str, Any]], *, plan_mode: bool) -> None:
        results: list[str | None] = [None] * len(tool_calls)
        pending: list[tuple[int, str, dict[str, Any]]] = []

        # Consent stays on the caller's thread so prompts cannot interleave.
        for index, call in enumerate(tool_calls):
            name, arguments = unpack(call)
            self.on_tool(name, arguments)
            refusal = self._veto(name, arguments, plan_mode=plan_mode)
            if refusal is not None:
                results[index] = refusal
            else:
                pending.append((index, name, arguments))

        # Only adjacent reads commute. Flush them before each mutating call.
        reads: list[tuple[int, str, dict[str, Any]]] = []
        for item in pending:
            if self._is_read_only(item[1]):
                reads.append(item)
                continue
            self._run_reads(reads, results)
            reads.clear()
            index, name, arguments = item
            results[index] = self._dispatch(name, arguments)
        self._run_reads(reads, results)

        for index, call in enumerate(tool_calls):
            name, _ = unpack(call)
            content = results[index] or ""
            self.on_tool_result(name, content)
            self.session.record({"role": "tool", "tool_call_id": call.get("id", ""), "content": content})

    def _run_reads(self, reads: list[tuple[int, str, dict[str, Any]]], results: list[str | None]) -> None:
        if not reads:
            return
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(reads))) as pool:
            futures = {pool.submit(self._dispatch, name, arguments): index for index, name, arguments in reads}
            for future, index in futures.items():
                results[index] = future.result()

    def _veto(self, name: str, arguments: dict[str, Any], *, plan_mode: bool) -> str | None:
        tool = self.registry.get(name)
        if tool is None:
            return None
        if plan_mode and not tool.read_only:
            return "refused: plan mode is on; only read-only tools may run"
        reason = self.hooks.run_pre(name, arguments)
        if reason is not None:
            return f"refused by pre hook: {reason}"
        return self.permissions.check(tool, arguments)

    def _dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        plan_revision = self.plan.revision
        result = self.registry.dispatch(name, arguments)
        if self.plan.revision != plan_revision:
            self.session.record_plan(self.plan.snapshot())
        self.hooks.run_post(name, arguments, result)
        return result

    def _is_read_only(self, name: str) -> bool:
        tool = self.registry.get(name)
        return bool(tool and tool.read_only)


def unpack(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    function = call.get("function", {})
    raw = function.get("arguments", "")
    if not raw:
        return function.get("name", ""), {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}
    return function.get("name", ""), parsed if isinstance(parsed, dict) else {}
