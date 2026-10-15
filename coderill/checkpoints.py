"""Session-scoped undo for the file-writing tools.

``edit_file`` and ``write_file`` record a checkpoint before touching a file;
``/undo`` pops the latest one and restores the previous bytes (or removes the
file if it did not exist). In-memory only: undo history dies with the process,
and only the two file-writing tools are tracked, not bash side effects.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# (path, prior bytes, digest immediately after our write)
_stack: list[tuple[Path, bytes | None, str | None]] = []


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def record(path: Path) -> None:
    """Snapshot a file just before it is modified."""
    _stack.append((path, path.read_bytes() if path.exists() else None, None))


def seal(path: Path) -> None:
    """Bind the checkpoint to the bytes actually written by this tool."""
    previous_path, before, _ = _stack[-1]
    if previous_path != path:
        raise ValueError("checkpoint path changed")
    _stack[-1] = (path, before, _digest(path))


def undo() -> str:
    """Restore the most recent checkpoint, or report that there is nothing to undo."""
    if not _stack:
        return "nothing to undo"
    path, previous, after = _stack[-1]
    if after is None or _digest(path) != after:
        return f"refused to undo {path.name}: file changed since CodeRill wrote it"
    _stack.pop()
    if previous is None:
        path.unlink(missing_ok=True)
        return f"removed {path.name} (it did not exist before)"
    path.parent.mkdir(parents=True, exist_ok=True)  # recreate a deleted parent tree
    path.write_bytes(previous)
    return f"restored {path.name}"


def pending() -> int:
    """How many checkpoints are available to undo."""
    return len(_stack)


def clear() -> None:
    _stack.clear()
