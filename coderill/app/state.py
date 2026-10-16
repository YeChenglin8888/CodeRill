"""Terminal view state and event callbacks."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field

from rich.console import Console

from ..agent.runtime import AgentRuntime
from ..agent.subagents import SubagentService
from ..config import Config
from ..hooks import Hooks
from ..llm import LLM
from ..mcp import MCPManager
from ..permissions import ReviewManager
from ..providers import create_provider
from ..providers.types import ChatStreamEvent
from ..runtime.services import ServiceProcessManager
from ..session import Session
from ..skills import SkillCatalog, default_roots
from ..taskplan import TaskPlan
from ..tools import Tool, build_registry
from ..tools.agent import BackgroundCancelTool, BackgroundNotificationsTool, BackgroundStatusTool, DelegateTool
from ..tools.archive import ArchiveReadTool
from ..tools.services import ProcessLogsTool, ProcessStartTool, ProcessStatusTool, ProcessStopTool
from ..tools.skills import LoadSkillTool
from ..tools.taskplan import task_plan_tools


@dataclass
class State:
    """Everything the REPL needs, so sessions can be swapped in place."""

    config: Config
    session: Session
    console: Console
    llm: LLM
    permission: ReviewManager
    hooks: Hooks
    mcp: MCPManager = field(default_factory=MCPManager)
    agent: AgentRuntime | None = None
    show_reasoning: bool = False
    plan_mode: bool = False
    pending_attachments: list[dict] = field(default_factory=list)
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.new_event_loop)
    subagent_services: dict[str, SubagentService] = field(default_factory=dict)
    service_processes: dict[str, ServiceProcessManager] = field(default_factory=dict)

    def rebuild(self, provider=None) -> None:
        registry = build_registry(self.config.workspace, llm=self.llm, config=self.config, extra_tools=self.mcp.tools)
        registry.register(ArchiveReadTool(self.session))
        service = self.subagent_services.get(self.session.id)
        if service is None:
            service = SubagentService(self.config, self.session)
            self.subagent_services[self.session.id] = service
        else:
            service.parent = self.session
            service.background.session = self.session
            service.config = self.config
        for tool in (DelegateTool(service), BackgroundStatusTool(service), BackgroundCancelTool(service),
                     BackgroundNotificationsTool(service)):
            registry.register(tool)
        plan = TaskPlan.from_snapshot(self.session.plan_snapshot) if self.session.plan_snapshot else TaskPlan()
        service.plan = plan
        if self.session.skill_snapshot is None:
            skills = SkillCatalog(default_roots(self.config.workspace))
        else:
            skills = SkillCatalog.from_snapshot(self.session.skill_snapshot, self.session)
        if skills:
            registry.register(LoadSkillTool(skills))
        for tool in task_plan_tools(plan):
            registry.register(tool)
        processes = self.service_processes.get(self.session.id)
        if processes is None:
            processes = ServiceProcessManager(self.session, self.config.workspace)
            self.service_processes[self.session.id] = processes
        else:
            processes.session = self.session
        for tool in (ProcessStartTool(processes), ProcessStatusTool(processes),
                     ProcessLogsTool(processes), ProcessStopTool(processes)):
            registry.register(tool)
        self.agent = AgentRuntime(
            self.config,
            self.session,
            registry,
            provider if provider is not None else create_provider(self.config),
            self.permission,
            hooks=self.hooks,
            skills=skills,
            plan=plan,
            mcp=self.mcp,
            collect_background=service.collect_background,
            on_event=self._on_event,
        )
        self.agent.plan_mode = self.plan_mode

    def select_model(self, profile: str) -> None:
        config = Config.from_env(self.config.workspace, overrides={"profile": profile, "benchmark": self.config.benchmark})
        provider = create_provider(config)
        previous_config, previous_llm = self.config, self.llm
        self.config, self.llm = config, LLM(config)
        try:
            self.rebuild(provider)
        except Exception:
            self.config, self.llm = previous_config, previous_llm
            raise
        self.session.set_model(config.model)

    def run_async(self, coroutine):
        return self.loop.run_until_complete(coroutine)

    def close(self) -> None:
        for service in self.subagent_services.values():
            self.loop.run_until_complete(service.close())
        self.loop.run_until_complete(self.mcp.close())
        self.loop.close()

    async def close_async(self) -> None:
        for service in self.subagent_services.values():
            await service.close()
        await self.mcp.close()

    @property
    def mcp_tools(self) -> list[Tool]:
        return self.mcp.tools

    def _on_event(self, event: ChatStreamEvent) -> None:
        if event.kind == "text_delta":
            _write(event.text)
        elif event.kind == "reasoning_delta":
            self._on_reasoning(event.text)
        elif event.kind == "tool_started" and event.tool_call:
            self._on_tool(event.tool_call.name, event.tool_call.arguments)
        elif event.kind == "tool_result":
            self._on_tool_result("", event.text)

    def _on_reasoning(self, text: str) -> None:
        if self.show_reasoning:
            self.console.print(f"[dim italic]{text}[/dim italic]", end="")

    def _on_tool(self, name: str, arguments: dict) -> None:
        preview = ", ".join(f"{k}={_short(v)}" for k, v in arguments.items())
        self.console.print(f"\n[magenta]→ {name}[/magenta] [dim]({preview})[/dim]")

    def _on_tool_result(self, name: str, text: str) -> None:
        first = _short(text.strip().splitlines()[0] if text.strip() else "(no output)", 100)
        self.console.print(f"[dim]← {first}[/dim]")


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _short(value: object, limit: int = 60) -> str:
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "..."
