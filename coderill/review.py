"""Trusted pre-write review: show the exact change before a file is mutated.

The permission layer decides *whether* a call may run; this builds the evidence a
human needs to decide well — a unified diff of the proposed change, computed from
the file's real current bytes. Shell commands are deliberately excluded: their
file effects cannot be precomputed safely.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from .sandbox import SandboxPolicy, check_active_mutation, policy_scope
from .tools.advanced import _apply_git, patch_targets
from .tools.base import ToolError, resolve_path


def build_prewrite_review(tool_name: str, arguments: dict[str, Any], root: str | Path,
                          policy: SandboxPolicy | None = None) -> str:
    """A unified diff for a file write/edit, or '' when it cannot be precomputed."""
    try:
        with policy_scope(policy):
            if tool_name == "write_file":
                return _for_write(arguments, Path(root))
            if tool_name == "edit_file":
                return _for_edit(arguments, Path(root))
            if tool_name == "delete_path":
                return _for_delete(arguments, Path(root))
            if tool_name == "apply_patch":
                patch = str(arguments.get("patch", ""))
                patch_targets(Path(root), patch, policy)
                _apply_git(Path(root), patch, "--check", policy=policy)
                return patch[:30_000]
    except (OSError, ValueError, ToolError):
        return ""
    return ""


def _for_write(arguments: dict[str, Any], root: Path) -> str:
    target = resolve_path(root, str(arguments.get("path", "")))
    before = target.read_text(errors="replace") if target.exists() else ""
    after = str(arguments.get("content", ""))
    return _diff(before, after, str(arguments.get("path", "")))


def _for_edit(arguments: dict[str, Any], root: Path) -> str:
    target = resolve_path(root, str(arguments.get("file_path", "")))
    if not target.exists():
        return ""
    before = target.read_text(errors="replace")
    old = str(arguments.get("old_string", ""))
    new = str(arguments.get("new_string", ""))
    count = before.count(old)
    if count == 0 or (count > 1 and not arguments.get("replace_all")):
        # The edit would not apply cleanly; there is nothing trustworthy to show.
        return ""
    after = before.replace(old, new, -1 if arguments.get("replace_all") else 1)
    return _diff(before, after, str(arguments.get("file_path", "")))


def _for_delete(arguments: dict[str, Any], root: Path) -> str:
    path = str(arguments.get("path", ""))
    target = resolve_path(root, path)
    check_active_mutation(target)
    if target == root.resolve() or not target.exists():
        return ""
    if target.is_file():
        return _diff(target.read_text(errors="replace"), "", path)
    if not arguments.get("recursive"):
        return ""
    files = [item for item in target.rglob("*") if item.is_file() and not item.is_symlink()]
    preview = [f"Delete directory {path} ({len(files)} files):"]
    preview.extend(_diff(item.read_text(errors="replace"), "", str(item.relative_to(root)))
                   for item in files[:20])
    return "\n".join(preview)[:30_000]


def _diff(before: str, after: str, filename: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            lineterm="",
        )
    )
