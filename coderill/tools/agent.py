"""The sub-agent tool.

Spawning a fresh agent for a self-contained sub-task keeps a long aside out of
the parent's context window: the child investigates with its own conversation
and returns only a short report. The tool is a thin wrapper around a callable the
parent agent supplies, which avoids a circular import and lets the parent decide
what tools the child gets.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from .base import Tool


class AgentTool(Tool):
    name = "agent"
    description = (
        "Delegate a self-contained sub-task to a fresh agent and get back a text report. "
        "Use it for research or multi-step work that would otherwise fill your context."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The complete task, with everything the sub-agent needs."},
        },
        "required": ["task"],
    }

    def __init__(self, runner: Callable[[str], str]) -> None:
        self._runner = runner

    def execute(self, task: str) -> str:
        return self._runner(task)
