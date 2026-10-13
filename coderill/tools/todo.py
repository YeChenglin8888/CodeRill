"""The agent's own task list.

For multi-step work the agent writes the whole checklist at once, then rewrites
it as items move from pending to in_progress to done. The list lives in memory
for the session, and the agent re-injects it into the conversation on every
round, so the model always works from the current state instead of digging it
out of old tool results.
"""

from __future__ import annotations

from typing import ClassVar

from .base import Tool, ToolError

_STATUSES = {"pending", "in_progress", "done"}
_MARK = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]"}


class TodoWriteTool(Tool):
    name = "todo_write"
    description = (
        "Write the session's task list. Pass the complete list every time, with each item "
        "marked pending, in_progress or done. Use it to plan multi-step work and track progress."
    )
    local_only = True
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "The full checklist, replacing any previous one.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "What the step is."},
                        "status": {"type": "string", "enum": sorted(_STATUSES)},
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["tasks"],
    }

    def __init__(self) -> None:
        self.tasks: list[dict[str, str]] = []

    def execute(self, tasks: list) -> str:
        cleaned = self._validate(tasks)
        self.tasks = cleaned
        done = sum(1 for t in cleaned if t["status"] == "done")
        return f"Task list updated ({done}/{len(cleaned)} done).\n\n{self.render()}"

    def render(self) -> str:
        if not self.tasks:
            return "(no tasks)"
        return "\n".join(f"{_MARK[t['status']]} {t['content']}" for t in self.tasks)

    @staticmethod
    def _validate(tasks: list) -> list[dict[str, str]]:
        if not isinstance(tasks, list):
            raise ToolError("tasks must be a list")
        cleaned = []
        for item in tasks:
            if not isinstance(item, dict) or not item.get("content") or item.get("status") not in _STATUSES:
                raise ToolError("each task needs a 'content' and a status of pending/in_progress/done")
            cleaned.append({"content": str(item["content"]), "status": item["status"]})
        return cleaned
