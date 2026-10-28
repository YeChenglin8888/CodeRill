"""The load_skill tool: pull a skill's full instructions into the conversation."""

from __future__ import annotations

from typing import ClassVar

from ..routing import search_capabilities
from ..skills import SkillCatalog
from .base import Tool, ToolError


class LoadSkillTool(Tool):
    name = "load_skill"
    description = (
        "Load a skill's full instructions by name. The system prompt lists the available "
        "skills with a one-line description each; call this when one applies to the task."
    )
    read_only = True
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact skill name from the skill catalogue."},
        },
        "required": ["name"],
    }

    def __init__(self, catalog: SkillCatalog) -> None:
        self.catalog = catalog

    def execute(self, name: str) -> str:
        try:
            return self.catalog.load(name)
        except KeyError:
            available = ", ".join(skill.name for skill in self.catalog.skills) or "(none)"
            raise ToolError(f"unknown skill '{name}'. Available: {available}") from None


class SkillSearchTool(Tool):
    name = "skill_search"
    description = "Search the full Skill catalogue by name or description before loading a Skill."
    read_only = True
    parameters: ClassVar[dict] = {
        "type": "object", "properties": {"query": {"type": "string"}},
        "required": ["query"], "additionalProperties": False,
    }

    def __init__(self, catalog: SkillCatalog, embedding: dict[str, str] | None = None) -> None:
        self.catalog = catalog
        self.embedding = embedding
        self.deadline: float | None = None

    def execute(self, query: str) -> str:
        import json

        items = [{"name": skill.name, "description": skill.description} for skill in self.catalog.skills]
        try:
            metadata: dict[str, str] = {}
            matches = search_capabilities(query, items, self.embedding, metadata, self.deadline)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return json.dumps({"matches": matches, **metadata}, ensure_ascii=False)
