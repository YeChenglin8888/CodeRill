"""Session-scoped undo for the file-writing tools.

``edit_file`` and ``write_file`` record a checkpoint before touching a file;
``/undo`` pops the latest one and restores the previous bytes (or removes the
file if it did not exist). In-memory only: undo history dies with the process,
and only the two file-writing tools are tracked, not bash side effects.
"""

from __future__ import annotations

from pathlib import Path

# (path, prior bytes, or None if the file did not exist yet)
_stack: list[tuple[Path, bytes | None]] = []


def record(path: Path) -> None:
    """Snapshot a file just before it is modified."""
    _stack.append((path, path.read_bytes() if path.exists() else None))


def undo() -> str:
    """Restore the most recent checkpoint, or report that there is nothing to undo."""
    if not _stack:
        return "nothing to undo"
    path, previous = _stack.pop()
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
