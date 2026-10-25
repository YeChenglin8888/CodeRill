"""Content search with regex support."""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from ..sandbox import check_active_path
from .base import Tool, ToolError, resolve_path

# Directories that are almost never worth searching.
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", "dist", "build", ".pytest_cache", ".ruff_cache"}
MAX_MATCHES = 200
MAX_FILE_BYTES = 1_000_000


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents with a regular expression, returning 'path:line: text' matches."
    read_only = True
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regular expression to search for."},
            "path": {"type": "string", "description": "File or directory to search. Default the workspace root."},
            "include": {"type": "string", "description": "Only search files matching this glob, e.g. '*.py'."},
        },
        "required": ["pattern"],
    }

    def __init__(self, root: Path) -> None:
        self.root = root

    def execute(self, pattern: str, path: str = ".", include: str | None = None) -> str:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid regex: {exc}") from None

        base = resolve_path(self.root, path)
        if not base.exists():
            raise ToolError(f"path not found: {path}")

        files = [base] if base.is_file() else _walk(base, include, self.root)
        matches: list[str] = []
        for file in files:
            for lineno, line in _search(file, regex):
                matches.append(f"{file.relative_to(self.root)}:{lineno}: {line}")
                if len(matches) >= MAX_MATCHES:
                    matches.append(f"... stopped after {MAX_MATCHES} matches")
                    return "\n".join(matches)
        return "\n".join(matches) if matches else f"no matches for {pattern!r}"


def _walk(root: Path, include: str | None, workspace: Path):
    workspace = workspace.resolve()
    for path in sorted(root.rglob("*")):
        if path.is_dir() or any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.resolve().is_relative_to(workspace):
            continue
        try:
            check_active_path(path)
        except RuntimeError:
            continue
        if include and not path.match(include):
            continue
        yield path


def _search(file: Path, regex: re.Pattern):
    try:
        check_active_path(file)
        if file.stat().st_size > MAX_FILE_BYTES:
            return
        text = file.read_text(errors="replace")
    except (OSError, ValueError):
        return
    for lineno, line in enumerate(text.splitlines(), start=1):
        if regex.search(line):
            yield lineno, line.strip()[:300]
