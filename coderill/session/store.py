"""JSONL event storage; no agent or UI dependencies."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path


class SessionStore:
    """Append-only JSONL storage, one file per session."""

    def __init__(self, root: str | Path | None = None) -> None:
        from . import SESSION_DIR

        self.root = Path(root) if root is not None else SESSION_DIR

    def path(self, session_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            raise ValueError("invalid session id")
        return self.root / f"{session_id}.jsonl"

    def blob_path(self, session_id: str, digest: str) -> Path:
        self.path(session_id)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid blob id")
        return self.root / f"{session_id}.assets" / digest

    def put_blob(self, session_id: str, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self.blob_path(session_id, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temporary = path.with_name(f".{digest}.{uuid.uuid4().hex}.tmp")
            try:
                with temporary.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return digest

    def read_blob(self, session_id: str, digest: str) -> bytes:
        return self.blob_path(session_id, digest).read_bytes()

    def append(self, session_id: str, event: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path(session_id)
        if path.exists():
            self._save_incomplete_tail(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def events(self, session_id: str) -> list[dict]:
        path = self.path(session_id)
        if not path.exists():
            return []
        events: list[dict] = []
        raw_lines = path.read_bytes().splitlines(keepends=True)
        for index, raw in enumerate(raw_lines):
            if not raw.strip():
                raise ValueError(f"blank event at line {index + 1} in {path}")
            try:
                event = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                if index == len(raw_lines) - 1 and not raw.endswith(b"\n"):
                    break
                raise ValueError(f"damaged event at line {index + 1} in {path}") from exc
            if not isinstance(event, dict) or event.get("v") not in (1, 2):
                raise ValueError(f"unsupported event at line {index + 1} in {path}")
            if event.get("seq") != len(events) + 1:
                raise ValueError(f"event sequence gap at line {index + 1} in {path}")
            events.append(event)
        return events

    def _save_incomplete_tail(self, path: Path) -> None:
        raw = path.read_bytes()
        if not raw or raw.endswith(b"\n"):
            return
        tail = raw.rsplit(b"\n", 1)[-1]
        try:
            json.loads(tail)
        except (UnicodeDecodeError, json.JSONDecodeError):
            backup = path.with_suffix(".incomplete-tail")
            with backup.open("ab") as handle:
                handle.write(tail + b"\n")
            with path.open("r+b") as handle:
                handle.truncate(len(raw) - len(tail))
        else:
            with path.open("ab") as handle:
                handle.write(b"\n")

    @contextmanager
    def driver_lock(self, session_id: str):
        """Only one process may drive a session at a time."""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{self.path(session_id).stem}.lock"
        with path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise RuntimeError(f"session {session_id} is already running") from exc
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise RuntimeError(f"session {session_id} is already running") from exc
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def ids(self) -> list[str]:
        if not self.root.exists():
            return []
        return [path.stem for path in self.root.glob("*.jsonl")]
