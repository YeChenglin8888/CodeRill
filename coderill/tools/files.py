"""File tools: read_file, write_file and edit_file.

The two writers take a checkpoint before they touch a file, so ``/undo`` can put
the previous bytes back.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import ClassVar

from .. import checkpoints
from .base import Tool, ToolError, resolve_path

MAX_READ_LINES = 2000
MAX_LINE_LENGTH = 2000


class ReadFileTool(Tool):
    """Read a file and return it with line numbers."""

    name = "read_file"
    description = "Read a file and return it with line numbers. Use offset/limit for large files."
    read_only = True
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to read, relative to the workspace root."},
            "offset": {"type": "integer", "description": "1-based line to start from. Optional."},
            "limit": {"type": "integer", "description": "Maximum number of lines to return. Optional."},
        },
        "required": ["path"],
    }

    def __init__(self, root: Path) -> None:
        self.root = root

    def execute(self, path: str, offset: int | None = None, limit: int | None = None) -> str:
        target = resolve_path(self.root, path)
        if not target.exists():
            raise ToolError(f"file not found: {path}")
        if target.is_dir():
            raise ToolError(f"{path} is a directory, not a file")

        lines = target.read_text(errors="replace").splitlines()
        total = len(lines)

        start = max((offset or 1) - 1, 0)
        end = start + (limit or MAX_READ_LINES)
        window = lines[start:end]
        if not window:
            return f"(no content at offset {offset})"

        numbered = []
        for number, line in enumerate(window, start=start + 1):
            if len(line) > MAX_LINE_LENGTH:
                line = line[:MAX_LINE_LENGTH] + "... [line truncated]"
            numbered.append(f"{number:>6}\t{line}")

        body = "\n".join(numbered)
        shown = f"lines {start + 1}-{start + len(window)} of {total}"
        return f"{body}\n\n[{shown}]"


class WriteFileTool(Tool):
    """Create a file, or overwrite one completely."""

    name = "write_file"
    description = "Create a file, or overwrite an existing one completely."
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to write, relative to the workspace root."},
            "content": {"type": "string", "description": "Full contents to write."},
        },
        "required": ["path", "content"],
    }

    def __init__(self, root: Path) -> None:
        self.root = root

    def execute(self, path: str, content: str) -> str:
        target = resolve_path(self.root, path)
        if target.is_dir():
            raise ToolError(f"{path} is a directory")

        checkpoints.record(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        target.write_text(content)
        checkpoints.seal(target)
        verb = "Overwrote" if existed else "Created"
        return f"{verb} {path} ({len(content.splitlines())} lines, {len(content)} chars)."


class EditFileTool(Tool):
    """Replace an exact, unique string in a file."""

    name = "edit_file"
    description = (
        "Replace an exact string in a file. old_string must match uniquely unless "
        "replace_all is true. Whitespace and indentation must match exactly."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "File to edit."},
            "old_string": {"type": "string", "description": "Exact text to replace; must match uniquely."},
            "new_string": {"type": "string", "description": "Text to replace it with."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence. Default false."},
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    def __init__(self, root: Path) -> None:
        self.root = root

    def execute(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        target = resolve_path(self.root, file_path)
        if not target.exists():
            raise ToolError(f"file not found: {file_path}")
        if old_string == new_string:
            raise ToolError("old_string and new_string are identical")

        text = target.read_text(errors="replace")
        count = text.count(old_string)
        if count == 0:
            raise ToolError("old_string was not found in the file")
        if count > 1 and not replace_all:
            raise ToolError(
                f"old_string appears {count} times; add more surrounding context to make it "
                "unique, or pass replace_all=true"
            )

        updated = text.replace(old_string, new_string, -1 if replace_all else 1)
        checkpoints.record(target)
        target.write_text(updated)
        checkpoints.seal(target)

        replaced = count if replace_all else 1
        diff = _unified_diff(text, updated, file_path)
        return f"Edited {file_path}: replaced {replaced} occurrence(s).\n\n{diff}"


def _unified_diff(old: str, new: str, filename: str, context: int = 3) -> str:
    """A small unified diff so the model (and the user) can see the change."""
    diff = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
        n=context,
    )
    return "\n".join(diff)
