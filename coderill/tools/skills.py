"""The load_skill tool: pull a skill's full instructions into the conversation."""

from __future__ import annotations

from typing import ClassVar

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
