"""Optional Jev decision tool for non-browser choices."""

from __future__ import annotations

import json
from typing import ClassVar

from ..jev import choose
from .base import Tool, ToolError


class JevDecisionTool(Tool):
    name = "jev_decide"
    description = "Choose among 2-8 fixed options using Jev; provide concise evidence, never secrets."
    parameters: ClassVar[dict] = {"type": "object", "properties": {
        "state": {"type": "string", "description": "Relevant evidence, up to 8000 characters."},
        "question": {"type": "string"},
        "options": {"type": "object", "additionalProperties": {"type": "string"}},
    }, "required": ["state", "question", "options"]}

    def __init__(self, key: str, model: str) -> None:
        self.key, self.model = key, model

    def execute(self, state: str, question: str, options: dict[str, str]) -> str:
        if not isinstance(state, str) or len(state) > 8000:
            raise ToolError("state must be a string of at most 8000 characters")
        try:
            return json.dumps(choose(state, question, options, key=self.key, model=self.model))
        except Exception as exc:
            raise ToolError(f"Jev decision failed: {exc}") from exc
