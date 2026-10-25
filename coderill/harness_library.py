"""Persistent, domain-scoped improvement harness with complete adopted trees."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from .harness_tree import base_harness, edit_harness, read_harness, write_harness

_DOMAIN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_ENTRY = re.compile(r"[0-9a-f]{12}\Z")


class ImprovementLibrary:
    def __init__(self, path: Path, project: str, domain: str = "general") -> None:
        if not _DOMAIN.fullmatch(domain):
            raise ValueError("domain must use lowercase letters, digits, '_' or '-'")
        self.project, self.domain = project, domain
        self.root = (path.with_suffix("") if path.suffix == ".jsonl" else path) / project / domain
        self.index = path if path.suffix == ".jsonl" else self.root / "entries.jsonl"

    def read(self) -> list[dict]:
        if not self.index.exists():
            return []
        entries = []
        for number, line in enumerate(self.index.read_text(encoding="utf-8").splitlines(), 1):
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    raise TypeError("expected object")
                if entry.get("project") != self.project or entry.get("domain", "general") != self.domain:
                    continue
                entry_id = entry.get("id")
                if entry_id is not None:
                    if not isinstance(entry_id, str) or not _ENTRY.fullmatch(entry_id):
                        raise ValueError("invalid entry id")
                    entry["harness"] = read_harness(self.root / "harnesses" / entry_id / "harness")
                elif "harness" not in entry and isinstance(entry.get("overlay"), str):
                    entry["harness"] = edit_harness(base_harness(), {"system_prompt": entry["overlay"]})
                if not isinstance(entry.get("harness"), dict):
                    raise TypeError("entry has no harness")
                entries.append(entry)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"invalid improvement library line {number}: {exc}") from exc
        return entries

    def add(self, experience: dict, *, selection: dict, result: Path) -> Path:
        if any(not isinstance(experience.get(key), str) or not experience[key].strip()
               for key in ("cue", "lesson", "support")):
            raise ValueError("experience needs a cue, lesson and exact supporting excerpt")
        entry_id = uuid.uuid4().hex[:12]
        entry_root = self.root / "harnesses" / entry_id
        write_harness(experience["harness"], entry_root / "harness")
        (entry_root / "EXPERIENCE.md").write_text(
            f"# Adopted harness {entry_id}\n\n"
            f"- cue: {experience['cue']}\n- lesson: {experience['lesson']}\n"
            f"- evidence: {experience['support']}\n"
            f"- edits: {experience.get('edits', '')}\n"
            f"- selected: {selection['choice']}\n- result: {result}\n", encoding="utf-8")
        entry = {"id": entry_id, "project": self.project, "domain": self.domain,
                 **experience, "selection": selection, "result": str(result)}
        self.index.parent.mkdir(parents=True, exist_ok=True)
        with self.index.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.rebuild_index()
        return entry_root

    def rebuild_index(self) -> Path:
        lines = [f"# Improvement harness: {self.project}/{self.domain}", "",
                 "Earlier adopted component trees and evidence-supported lessons.", ""]
        for entry in self.read():
            lines.extend((f"- `{entry.get('id', 'legacy')}`: {entry.get('cue', '')} — {entry.get('lesson', '')}", ""))
        self.root.mkdir(parents=True, exist_ok=True)
        index = self.root / "INDEX.md"
        index.write_text("\n".join(lines), encoding="utf-8")
        return index
