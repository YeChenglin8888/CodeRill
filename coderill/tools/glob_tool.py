"""File pattern matching."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from ..sandbox import check_active_path
from .base import Tool, ToolError, resolve_path

MAX_RESULTS = 500


class GlobTool(Tool):
    name = "glob"
    description = "Find files matching a glob pattern, e.g. '**/*.py'. Returns paths, newest first."
    read_only = True
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py'."},
            "path": {"type": "string", "description": "Directory to search from. Default the workspace root."},
        },
        "required": ["pattern"],
    }

    def __init__(self, root: Path) -> None:
        self.root = root

    def execute(self, pattern: str, path: str = ".") -> str:
        base = resolve_path(self.root, path)
        if not base.exists():
            raise ToolError(f"directory not found: {path}")
        if not base.is_dir():
            raise ToolError(f"{path} is not a directory")

        matches = []
        workspace = self.root.resolve()
        for p in base.glob(pattern):
            if not p.is_file() or ".git" in p.parts:
                continue
            if not p.resolve().is_relative_to(workspace):
                continue
            try:
                check_active_path(p)
            except RuntimeError:
                continue
            matches.append(p)
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not matches:
            return f"no files match {pattern!r}"

        shown = matches[:MAX_RESULTS]
        lines = [str(p.relative_to(self.root)) for p in shown]
        if len(matches) > len(shown):
            lines.append(f"... and {len(matches) - len(shown)} more")
        return "\n".join(lines)
