"""Read-only Git views with bounded output and workspace paths."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import ClassVar

from .base import Tool, ToolError, resolve_path


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-c", "core.pager=cat", *args], cwd=root, capture_output=True,
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

    def __init__(self, root: Path) -> None:
        self.root = root

    def execute(self) -> str:
        return _git(self.root, "status", "--short") or "(clean worktree)"


class GitDiffTool(Tool):
    name = "git_diff"
    description = "Show unstaged or staged Git changes for a workspace path."
    read_only = True
    parameters: ClassVar[dict] = {"type": "object", "properties": {
        "path": {"type": "string"}, "staged": {"type": "boolean"},
    }}

    def __init__(self, root: Path) -> None:
        self.root = root

    def execute(self, path: str = ".", staged: bool = False) -> str:
        return _git(self.root, "diff", "--no-ext-diff", *(["--cached"] if staged else []),
                    "--", _relative(self.root, path)) or "(no diff)"


class GitLogTool(Tool):
    name = "git_log"
    description = "Show recent commits for a workspace path."
    read_only = True
    parameters: ClassVar[dict] = {"type": "object", "properties": {
        "path": {"type": "string"}, "max_entries": {"type": "integer"},
    }}

    def __init__(self, root: Path) -> None:
        self.root = root

    def execute(self, path: str = ".", max_entries: int = 10) -> str:
        if not 1 <= max_entries <= 100:
            raise ToolError("max_entries must be between 1 and 100")
        return _git(self.root, "log", "--oneline", f"-{max_entries}", "--", _relative(self.root, path)) or "(no commits)"
