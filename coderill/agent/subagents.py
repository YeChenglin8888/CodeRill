"""Bounded child agents with separate sessions and optional Git worktrees."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..config import Config
from ..permissions import Mode, ReviewManager
from ..providers import create_provider
from ..runtime import BackgroundTaskManager
from ..session import Session
from ..taskplan import TaskPlan
from ..tools import Registry
from ..tools.advanced import ApplyPatchTool, DeletePathTool, DiagnosticsTool, ReadMultiTool
from ..tools.archive import ArchiveReadTool
from ..tools.files import EditFileTool, ReadFileTool, WriteFileTool
from ..tools.git import GitDiffTool, GitLogTool, GitStatusTool
from ..tools.glob_tool import GlobTool
from ..tools.grep import GrepTool
from .runtime import AgentRuntime


class SubagentService:
    def __init__(
        self,
        config: Config,
        parent: Session,
        *,
        provider_factory: Callable[[Config], Any] = create_provider,
        plan: TaskPlan | None = None,
    ) -> None:
        self.config = config
        self.parent = parent
        self.provider_factory = provider_factory
        self.plan = plan
        self.background = BackgroundTaskManager(parent)
        self.children: dict[str, AgentRuntime] = {}

    async def delegate(self, task: str, *, allow_write: bool = False, background: bool = False,
                       plan_task_id: str | None = None) -> str:
        if plan_task_id is not None and not background:
            raise ValueError("plan_task_id requires background delegation")
        if plan_task_id is not None and (self.plan is None or
                                         not any(item.id == plan_task_id for item in self.plan.tasks)):
            raise ValueError(f"unknown plan task {plan_task_id}")
        child = Session.new(self.config.model, str(self.config.workspace), self.parent.store)
        workspace = await self._worktree(child.id) if allow_write else self.config.workspace
        if allow_write:
            child.set_workspace(str(workspace))
        child_config = replace(self.config, workspace=workspace, max_turns=min(self.config.max_turns, 10))
        registry = Registry(workspace)
        for tool in (ReadFileTool(workspace), ReadMultiTool(workspace), GlobTool(workspace), GrepTool(workspace),
                     GitStatusTool(workspace), GitDiffTool(workspace), GitLogTool(workspace), ArchiveReadTool(child)):
            registry.register(tool)
        if allow_write:
            registry.register(WriteFileTool(workspace))
            registry.register(EditFileTool(workspace))
            registry.register(ApplyPatchTool(workspace))
            registry.register(DeletePathTool(workspace))
            registry.register(DiagnosticsTool(workspace))
        runtime = AgentRuntime(
            child_config, child, registry, self.provider_factory(child_config),
            ReviewManager(workspace, mode=Mode.AGGRESSIVE),
        )
        self.children[child.id] = runtime
        self.parent.record_subagent({
            "id": child.id, "workspace": str(workspace), "allow_write": allow_write,
            "applied": False, "task": task,
        })

        async def work() -> str:
            result = await runtime.run_turn(task)
            if result.status != "complete":
                raise RuntimeError(f"child session {child.id}: {result.status}: {result.error or result.text}")
            return f"child session {child.id}: {result.status}\n{result.text or result.error}"

        if background:
            job_id = self.background.start(
                task, child.id, work, plan_task_id=plan_task_id,
                plan_revision=self.plan.revision if self.plan is not None else None,
            )
            return f"background task {job_id}; child session {child.id}; workspace {workspace}"
        return await work() + f"\nworkspace {workspace}"

    def collect_background(self) -> list[dict]:
        for job in self.parent.background_tasks.values():
            if job.get("notified") or job["status"] != "complete" or self.plan is None:
                continue
            task = next((item for item in self.plan.tasks if item.id == job.get("plan_task_id")), None)
            if task is None or task.status not in ("pending", "in_progress"):
                continue
            if task.status == "pending" and (job.get("plan_revision") != self.plan.revision
                                              or task.id not in self.plan.ready()):
                continue
            self.plan.update(task.id, self.plan.revision, status="done")
            self.parent.record_plan(self.plan.snapshot())
        return self.background.notifications(inject=True)

    async def _worktree(self, child_id: str) -> Path:
        root = await _git(self.config.workspace, "rev-parse", "--show-toplevel")
        if root.returncode != 0:
            raise ValueError("writable child agents require a Git repository")
        path = (self.parent.store.root.parent / "worktrees" / child_id).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        created = await _git(self.config.workspace, "worktree", "add", "--detach", str(path), "HEAD")
        if created.returncode != 0:
            raise RuntimeError(created.stderr.decode(errors="replace"))
        return path

    async def preview_apply(self, child_id: str) -> str:
        child = self.parent.subagents[child_id]
        if not child["allow_write"] or child["applied"]:
            raise ValueError("child has no unapplied worktree changes")
        path = Path(child["workspace"])
        staged = await _git(path, "add", "-N", ".")
        if staged.returncode != 0:
            raise RuntimeError(staged.stderr.decode(errors="replace"))
        diff = await _git(path, "diff", "--binary", "HEAD")
        if diff.returncode != 0:
            raise RuntimeError(diff.stderr.decode(errors="replace"))
        return diff.stdout.decode(errors="replace")

    async def apply(self, child_id: str, reviewed_diff: str) -> str:
        actual = await self.preview_apply(child_id)
        if actual != reviewed_diff:
            raise ValueError("worktree changed since review; inspect a new diff")
        if not actual:
            return "nothing to apply"
        checked = await _git(self.config.workspace, "apply", "--check", "-", input=actual.encode())
        if checked.returncode != 0:
            raise RuntimeError(f"cannot apply changes: {checked.stderr.decode(errors='replace')}")
        applied = await _git(self.config.workspace, "apply", "-", input=actual.encode())
        if applied.returncode != 0:
            raise RuntimeError(f"apply failed: {applied.stderr.decode(errors='replace')}")
        self.parent.mark_subagent_applied(child_id)
        return f"applied changes from child {child_id}"

    async def close(self) -> None:
        for child in self.children.values():
            child.cancel_turn()
        await self.background.close()


async def _git(cwd: Path, *args: str, input: bytes | None = None) -> subprocess.CompletedProcess:
    process = await asyncio.create_subprocess_exec(
        "git", *args, cwd=cwd, stdin=asyncio.subprocess.PIPE if input is not None else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(input)
    return subprocess.CompletedProcess(["git", *args], process.returncode, stdout, stderr)
