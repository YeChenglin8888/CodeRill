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

from .base import Tool, ToolError


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


class DelegateTool(Tool):
    name = "agent"
    description = "Delegate to an isolated child session. Writable children use a Git worktree."
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "allow_write": {"type": "boolean"},
            "background": {"type": "boolean"},
            "plan_task_id": {"type": "string", "description": "Optional existing task plan ID to update on completion."},
        },
        "required": ["task"],
    }

    def __init__(self, service) -> None:
        self.service = service

    async def aexecute(self, task: str, allow_write: bool = False, background: bool = False,
                       plan_task_id: str | None = None) -> str:
        return await self.service.delegate(task, allow_write=allow_write, background=background,
                                           plan_task_id=plan_task_id)

    def execute(self, **kwargs) -> str:
        raise ToolError("delegation requires the async runtime")


class BackgroundStatusTool(Tool):
    name = "background_status"
    description = "Check a background child agent by task id."
    read_only = True
    parameters: ClassVar[dict] = {
        "type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"],
    }

    def __init__(self, service) -> None:
        self.service = service

    def execute(self, task_id: str) -> str:
        return str(self.service.background.status(task_id))


class BackgroundCancelTool(Tool):
    name = "background_cancel"
    description = "Cancel a running background child agent by task id."
    local_only = True
    parameters: ClassVar[dict] = {
        "type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"],
    }

    def __init__(self, service) -> None:
        self.service = service

    async def aexecute(self, task_id: str) -> str:
        self.service.background.cancel(task_id)
        return f"cancelled background task {task_id}"

    def execute(self, **kwargs) -> str:
        raise ToolError("background cancellation requires the async runtime")


class BackgroundNotificationsTool(Tool):
    name = "background_notifications"
    description = "Collect completed background task notifications, once each."
    local_only = True
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}

    def __init__(self, service) -> None:
        self.service = service

    async def aexecute(self) -> str:
        return str(self.service.background.notifications())

    def execute(self, **kwargs) -> str:
        raise ToolError("background notifications require the async runtime")
