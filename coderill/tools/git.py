"""Read-only Git views with bounded output and workspace paths."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import ClassVar

from ..sandbox import DockerJob, SandboxPolicy
from .base import Tool, ToolError, resolve_path


def _git(root: Path, *args: str, policy: SandboxPolicy | None = None) -> str:
    try:
        argv = ["git", "-c", "core.pager=cat", *(["-c", "core.hooksPath=/dev/null"] if policy and policy.enabled else []), *args]
        if policy and policy.enabled:
            raw = DockerJob(policy).run(argv, timeout=20)
            result = subprocess.CompletedProcess(argv, raw.returncode, raw.stdout.decode(errors="replace"),
                                                 raw.stderr.decode(errors="replace"))
        else:
            result = subprocess.run(argv, cwd=root, capture_output=True,
                                    text=True, errors="replace", timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"git failed: {exc}") from exc
    if result.returncode:
        raise ToolError(result.stderr.strip() or f"git exited {result.returncode}")
    return result.stdout[:30_000]


def _relative(root: Path, path: str) -> str:
    target = resolve_path(root, path)
    return "." if target == root.resolve() else target.relative_to(root.resolve()).as_posix()


class GitStatusTool(Tool):
    name = "git_status"
    description = "Show the current Git worktree status."
    read_only = True
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}

    def __init__(self, root: Path, policy: SandboxPolicy | None = None) -> None:
        self.root = root
        self.policy = policy

    def execute(self) -> str:
        return _git(self.root, "status", "--short", policy=self.policy) or "(clean worktree)"


class GitDiffTool(Tool):
    name = "git_diff"
    description = "Show unstaged or staged Git changes for a workspace path."
    read_only = True
    parameters: ClassVar[dict] = {"type": "object", "properties": {
        "path": {"type": "string"}, "staged": {"type": "boolean"},
    }}

    def __init__(self, root: Path, policy: SandboxPolicy | None = None) -> None:
        self.root = root
        self.policy = policy

    def execute(self, path: str = ".", staged: bool = False) -> str:
        excludes = [f":(exclude){denied.relative_to(self.root).as_posix()}"
                    for denied in self.policy._denied] if self.policy and self.policy.enabled else []
        return _git(self.root, "diff", "--no-ext-diff", *(["--cached"] if staged else []),
                    "--", _relative(self.root, path), *excludes, policy=self.policy) or "(no diff)"


class GitLogTool(Tool):
    name = "git_log"
    description = "Show recent commits for a workspace path."
    read_only = True
    parameters: ClassVar[dict] = {"type": "object", "properties": {
        "path": {"type": "string"}, "max_entries": {"type": "integer"},
    }}

    def __init__(self, root: Path, policy: SandboxPolicy | None = None) -> None:
        self.root = root
        self.policy = policy

    def execute(self, path: str = ".", max_entries: int = 10) -> str:
        if not 1 <= max_entries <= 100:
            raise ToolError("max_entries must be between 1 and 100")
        return _git(self.root, "log", "--oneline", f"-{max_entries}", "--", _relative(self.root, path),
                    policy=self.policy) or "(no commits)"
