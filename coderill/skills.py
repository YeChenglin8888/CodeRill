"""File-based reusable workflow instructions ("skills").

A skill is a reusable set of instructions the model can pull in when it needs
them — how to write a changelog here, how to structure a migration, and so on.

Discovery rules:
  * project roots are searched before global ones, and the first skill with a
    given name wins, so a project can override a global skill;
  * a skill is either ``<root>/<name>/SKILL.md`` or a single ``<root>/<name>.md``;
  * ``INDEX.md`` is a human catalogue, not a callable skill.

The catalogue exposes only each skill's name and a one-line description, so the
system prompt stays small. The full body enters the conversation only when the
model calls ``load_skill``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAX_INDEX_CHARS = 8000
MAX_DESCRIPTION_CHARS = 240


def default_roots(project_root: str | Path) -> list[Path]:
    """Discovery roots, most specific first."""
    return [
        Path(project_root) / ".agents" / "skills",
        Path(project_root) / "skills",
        Path.home() / ".agents" / "skills",
        Path.home() / ".coderill" / "skills",
    ]


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    content: str


class SkillCatalog:
    """The skills discovered under a set of roots."""

    def __init__(self, roots: list[str | Path] | None = None) -> None:
        self.roots = [Path(root) for root in (roots or [])]
        self.skills: list[Skill] = self._discover()

    @classmethod
    def from_snapshot(cls, entries: list[dict], session) -> SkillCatalog:
        catalog = cls([])
        catalog.skills = [Skill(entry["name"], entry["description"], Path(entry["path"]),
                                session.store.read_blob(session.id, entry["digest"]).decode("utf-8"))
                          for entry in entries]
        return catalog

    def _discover(self) -> list[Skill]:
        found: dict[str, Skill] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for skill_file in _skill_files(root):
                if not skill_file.resolve().is_relative_to(root.resolve()):
                    continue
                skill = _parse(skill_file)
                if skill and skill.name not in found:
                    found[skill.name] = skill
        return sorted(found.values(), key=lambda skill: skill.name)

    def render(self, budget: int = MAX_INDEX_CHARS) -> str:
        """The catalogue as shown to the model: name plus one line each."""
        lines: list[str] = []
        used = 0
        for skill in self.skills:
            line = f"- {skill.name}: {skill.description}"
            if used + len(line) > budget:
                lines.append("- [more skills omitted]")
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    def load(self, name: str) -> str:
        """The full text of a skill, by exact name."""
        for skill in self.skills:
            if skill.name == name:
                return skill.content
        raise KeyError(name)

    def __bool__(self) -> bool:
        return bool(self.skills)


def _skill_files(root: Path) -> list[Path]:
    files = [p / "SKILL.md" for p in sorted(root.glob("*")) if (p / "SKILL.md").is_file()]
    files += [p for p in sorted(root.glob("*.md")) if p.is_file()]
    return files


def _parse(path: Path) -> Skill | None:
    if path.stem.lower() == "index":
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    name, description = _frontmatter(text)
    if not name:
        name = path.parent.name if path.name == "SKILL.md" else path.stem
    if not description:
        description = _first_line(text)
    return Skill(name=name, description=description[:MAX_DESCRIPTION_CHARS], path=path, content=text)


def _frontmatter(text: str) -> tuple[str | None, str | None]:
    """Pull name/description out of optional leading ``---`` frontmatter."""
    if not text.startswith("---"):
        return None, None
    end = text.find("\n---", 3)
    if end == -1:
        return None, None

    name = description = None
    for line in text[3:end].splitlines():
        line = line.strip()
        if line.startswith("name:"):
            name = line[5:].strip().strip("\"'")
        elif line.startswith("description:"):
            description = line[12:].strip().strip("\"'")
    return name, description


def _first_line(text: str) -> str:
    """First non-empty, non-heading line — a cheap description fallback."""
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped and not stripped.startswith("---"):
            return stripped
    return ""
