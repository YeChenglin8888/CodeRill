"""Project-scoped, evidence-backed memories and local retrieval."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .session.share import redact


def _terms(value: str) -> set[str]:
    value = value.casefold()
    words = set(re.findall(r"[a-z0-9_./-]+", value))
    for run in re.findall(r"[\u3400-\u9fff]+", value):
        words.update(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return words


class MemoryStore:
    def __init__(self, workspace: Path, root: Path | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        project = hashlib.sha256(str(self.workspace).encode()).hexdigest()[:16]
        self.path = (root or Path.home() / ".coderill" / "memory") / f"{project}.sqlite3"
        self._model = None
        self._model_checked = False

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY, memory_key TEXT NOT NULL, kind TEXT NOT NULL,
            content TEXT NOT NULL, evidence TEXT NOT NULL, source TEXT NOT NULL,
            source_type TEXT NOT NULL, created TEXT NOT NULL, status TEXT NOT NULL,
            previous_id TEXT, fingerprint TEXT NOT NULL UNIQUE)""")
        db.execute("CREATE TABLE IF NOT EXISTS embeddings (id TEXT PRIMARY KEY, vector TEXT NOT NULL)")
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, *, key: str, kind: str, content: str, evidence: str, source: str,
            source_type: str, update: bool = False) -> str | None:
        if (not all(isinstance(x, str) and x.strip() for x in (key, kind, content, evidence, source, source_type))
                or len(content) > 2000 or len(evidence) > 1000 or redact(content + evidence) != content + evidence):
            return None
        fingerprint = hashlib.sha256((source + "\0" + evidence + "\0" + content).encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM memories WHERE source=? AND status='deleted' LIMIT 1", (source,)).fetchone():
                return None
            existing = db.execute("SELECT id, status FROM memories WHERE fingerprint=?", (fingerprint,)).fetchone()
            if existing:
                return existing["id"] if existing["status"] in ("active", "conflict") else None
            previous = db.execute("SELECT id, content FROM memories WHERE memory_key=? AND status IN ('active','conflict') ORDER BY created DESC LIMIT 1", (key,)).fetchone()
            if previous and previous["content"] == content and not update:
                return previous["id"]
            if previous and update:
                db.execute("UPDATE memories SET status='superseded' WHERE memory_key=? AND status IN ('active','conflict')", (key,))
            status = "conflict" if previous and not update else "active"
            item_id = uuid.uuid4().hex[:12]
            db.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?)", (item_id, key, kind, content,
                evidence, source, source_type, now, status, previous["id"] if previous else None, fingerprint))
            return item_id

    def list(self, *, include_inactive: bool = False) -> list[dict]:
        if not self.path.exists():
            return []
        with self._connect() as db:
            rows = db.execute("SELECT * FROM memories" + ("" if include_inactive else " WHERE status IN ('active','conflict')") + " ORDER BY created DESC").fetchall()
            return [dict(row) for row in rows]

    def get(self, item_id: str) -> dict | None:
        return next((item for item in self.list() if item["id"] == item_id), None)

    def has_source(self, source: str) -> bool:
        if not self.path.exists():
            return False
        with self._connect() as db:
            return db.execute("SELECT 1 FROM memories WHERE source=? LIMIT 1", (source,)).fetchone() is not None

    def delete(self, item_id: str) -> bool:
        if not self.path.exists():
            return False
        with self._connect() as db:
            deleted = db.execute("UPDATE memories SET status='deleted', content='', evidence='' WHERE id=? AND status!='deleted'", (item_id,)).rowcount
            db.execute("DELETE FROM embeddings WHERE id=?", (item_id,))
            return bool(deleted)

    def correct(self, item_id: str, content: str) -> str | None:
        old = self.get(item_id)
        if not old:
            return None
        return self.add(key=old["memory_key"], kind=old["kind"], content=content, evidence=content,
                        source=f"user-correction:{item_id}:{uuid.uuid4().hex}", source_type="user", update=True)

    def import_history(self, session_store) -> int:
        """Index legacy conversation messages with stable event references."""
        count = 0
        for session_id in session_store.ids():
            try:
                events = session_store.events(session_id)
                start = next(event for event in events if event.get("type") == "start")
                if Path(start.get("workspace", "")).resolve() != self.workspace:
                    continue
                for event in events:
                    message = event.get("message", {})
                    content = message.get("content")
                    if (event.get("type") != "message" or message.get("role") not in ("user", "assistant")
                            or message.get("synthetic") or not isinstance(content, str) or len(content.strip()) < 12):
                        continue
                    excerpt = content[:1000]
                    source = f"session:{session_id}:event:{event['seq']}"
                    if self.has_source(source):
                        continue
                    item_id = self.add(key=source, kind="episode", content=excerpt, evidence=excerpt,
                                       source=source, source_type=message["role"])
                    count += item_id is not None
            except (OSError, ValueError, StopIteration, KeyError):
                continue
        from .harness_library import ImprovementLibrary
        project = hashlib.sha256(str(self.workspace).encode()).hexdigest()[:16]
        for domain in ("code", "general"):
            library = ImprovementLibrary(Path.home() / ".coderill" / "improvements", project, domain)
            try:
                for entry in library.read():
                    entry_id = entry.get("id")
                    source = (f"harness:{library.root / 'harnesses' / entry_id}" if entry_id
                              else f"harness:{library.root}:legacy")
                    if self.has_source(source):
                        continue
                    count += self.add(key=entry.get("cue", ""), kind="experience", content=entry.get("lesson", ""),
                                      evidence=entry.get("support", ""), source=source, source_type="harness") is not None
            except (OSError, ValueError):
                continue
        return count

    def _embedding_model(self):
        if not self._model_checked:
            self._model_checked = True
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer("BAAI/bge-m3", local_files_only=True, device="cpu")
            except Exception:  # noqa: BLE001 - optional model; lexical retrieval stays available
                self._model = None
        return self._model

    def search(self, query: str, limit: int = 5) -> list[dict]:
        items = self.list()
        if not query.strip() or not items:
            return []
        terms = _terms(query)
        ranked = []
        for item in items:
            overlap = len(terms & _terms(item["content"] + " " + item["memory_key"]))
            if overlap:
                ranked.append((item["id"], overlap / max(len(terms), 1)))
        lexical = sorted(ranked, key=lambda row: row[1], reverse=True)
        scores = {item_id: 1 / (60 + rank) for rank, (item_id, _) in enumerate(lexical, 1)}
        model = self._embedding_model()
        if model:
            try:
                with self._connect() as db:
                    cached = {row["id"]: json.loads(row["vector"]) for row in db.execute("SELECT id, vector FROM embeddings")}
                    missing = [item for item in items if item["id"] not in cached]
                    vectors = model.encode([query] + [item["content"] for item in missing], normalize_embeddings=True)
                    for item, vector in zip(missing, vectors[1:]):
                        cached[item["id"]] = [float(value) for value in vector]
                        db.execute("INSERT OR REPLACE INTO embeddings VALUES (?,?)", (item["id"], json.dumps(cached[item["id"]])))
                semantic = sorted(((item["id"], float(sum(a * b for a, b in zip(vectors[0], cached[item["id"]]))))
                                   for item in items), key=lambda row: row[1], reverse=True)
                for rank, (item_id, similarity) in enumerate(semantic[:20], 1):
                    if math.isfinite(similarity) and similarity >= 0.4:
                        scores[item_id] = scores.get(item_id, 0) + 1 / (60 + rank)
            except Exception:  # noqa: BLE001 - optional model inference can fail independently
                self._model = None
        lookup = {item["id"]: item for item in items}
        return [lookup[item_id] for item_id in sorted(scores, key=scores.get, reverse=True)[:max(1, min(limit, 20))]]


def format_memories(items: list[dict]) -> str:
    return json.dumps([{key: item[key] for key in ("id", "kind", "content", "source", "source_type", "created", "status")}
                       for item in items], ensure_ascii=False)
