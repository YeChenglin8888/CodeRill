"""The sub-agent tool.

Spawning a fresh agent for a self-contained sub-task keeps a long aside out of
the parent's context window: the child investigates with its own conversation
and returns only a short report. The tool is a thin wrapper around a callable the
parent agent supplies, which avoids a circular import and lets the parent decide
what tools the child gets.
"""

from __future__ import annotations

from typing import ClassVar

from .base import Tool, ToolError


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


class HarnessDelegateTool(DelegateTool):
    """Expose only named, read-only harness roles inside a refinement probe."""

    name = "harness_agent"
    description = "Delegate a bounded investigation to a named harness subagent role."

    def __init__(self, service, roles: dict[str, str]) -> None:
        super().__init__(service)
        self.roles = roles
        self.parameters = {"type": "object", "properties": {
            "role": {"type": "string", "enum": sorted(roles)},
            "task": {"type": "string"},
        }, "required": ["role", "task"]}

    async def aexecute(self, role: str, task: str) -> str:
        return await self.service.delegate(task, harness_role=role)


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
