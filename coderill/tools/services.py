"""Agent tools for long-running local processes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, ClassVar

from .base import Tool, ToolError

if TYPE_CHECKING:
    from ..runtime.services import ServiceProcessManager


class ProcessStartTool(Tool):
    name = "process_start"
    description = "Start a long-running local command and save stdout/stderr to session logs."
    parameters: ClassVar[dict] = {"type": "object", "properties": {"command": {"type": "string"}},
                                   "required": ["command"]}

    def __init__(self, manager: ServiceProcessManager) -> None:
        self.manager = manager

    def execute(self, command: str) -> str:
        if not command.strip():
            raise ToolError("command must not be blank")
        return json.dumps(self.manager.start(command))


class ProcessStatusTool(Tool):
    name = "process_status"
    description = "Check a long-running process started in this session."
    read_only = True
    parameters: ClassVar[dict] = {"type": "object", "properties": {"process_id": {"type": "string"}},
                                   "required": ["process_id"]}

    def __init__(self, manager: ServiceProcessManager) -> None:
        self.manager = manager

    def execute(self, process_id: str) -> str:
        try:
            return json.dumps(self.manager.status(process_id))
        except KeyError:
            raise ToolError("unknown process id") from None


class ProcessLogsTool(ProcessStatusTool):
    name = "process_logs"
    description = "Read the recent stdout and stderr logs of a session process."

    def execute(self, process_id: str) -> str:
        try:
            return self.manager.logs(process_id)
        except KeyError:
            raise ToolError("unknown process id") from None


class ProcessStopTool(ProcessStatusTool):
    name = "process_stop"
    description = "Stop a process started by this runner, including its process group."
    read_only = False

    def execute(self, process_id: str) -> str:
        try:
            return json.dumps(self.manager.stop(process_id))
        except (KeyError, ValueError) as exc:
            raise ToolError(str(exc)) from None
