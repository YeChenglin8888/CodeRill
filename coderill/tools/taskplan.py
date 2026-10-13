"""The task-plan tools: task_list, task_create, task_update, task_revise.

All four change only in-process state, so they never need consent. Every write
carries an ``expected_revision``; a stale one comes back as an ordinary tool
error so the model re-reads and retries instead of overwriting a newer plan.
"""

from __future__ import annotations

from typing import ClassVar

from ..taskplan import StaleRevision, TaskPlan, UnknownTask
from .base import Tool, ToolError


class _PlanTool(Tool):
    local_only = True

    def __init__(self, plan: TaskPlan) -> None:
        self.plan = plan

    def _run(self, action):
        try:
            return action()
        except StaleRevision as exc:
            raise ToolError(str(exc)) from None
        except UnknownTask as exc:
            raise ToolError(str(exc)) from None
        except ValueError as exc:
            raise ToolError(str(exc)) from None


class TaskListTool(_PlanTool):
    name = "task_list"
    description = "Read the current task plan and its revision. Call this before any write."
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}

    def execute(self) -> str:
        return self.plan.describe()


class TaskCreateTool(_PlanTool):
    name = "task_create"
    description = (
        "Create the task plan, or append tasks to it. Pass the current revision; "
        "the reply reports the new revision. mode is 'linear' or 'dag'."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "contents": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One string per task to add.",
            },
            "expected_revision": {"type": "integer", "description": "Revision from task_list."},
            "mode": {"type": "string", "enum": ["linear", "dag"], "description": "Optional plan shape."},
        },
        "required": ["contents", "expected_revision"],
    }

    def execute(self, contents: list, expected_revision: int, mode: str | None = None) -> str:
        return self._run(lambda: self.plan.create(contents, expected_revision, mode))


class TaskUpdateTool(_PlanTool):
    name = "task_update"
    description = "Change a task's status, owner or dependencies by its stable ID."
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Stable task ID, e.g. 't2'."},
            "expected_revision": {"type": "integer"},
            "status": {"type": "string", "enum": ["pending", "in_progress", "done", "blocked"]},
            "owner": {"type": "string"},
            "depends_on": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["task_id", "expected_revision"],
    }

    def execute(
        self,
        task_id: str,
        expected_revision: int,
        status: str | None = None,
        owner: str | None = None,
        depends_on: list | None = None,
    ) -> str:
        return self._run(lambda: self.plan.update(task_id, expected_revision, status, owner, depends_on))


class TaskReviseTool(_PlanTool):
    name = "task_revise"
    description = "Reword a task when its meaning changed. Do not use it to report progress."
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "content": {"type": "string", "description": "The new wording."},
            "expected_revision": {"type": "integer"},
        },
        "required": ["task_id", "content", "expected_revision"],
    }

    def execute(self, task_id: str, content: str, expected_revision: int) -> str:
        return self._run(lambda: self.plan.revise(task_id, content, expected_revision))


def task_plan_tools(plan: TaskPlan) -> list[Tool]:
    return [TaskListTool(plan), TaskCreateTool(plan), TaskUpdateTool(plan), TaskReviseTool(plan)]
