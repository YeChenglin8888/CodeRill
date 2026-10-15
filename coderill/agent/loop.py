"""The agent loop.

One user turn drives a small state machine:

    model streams a reply
        -> if it asked for tools, run them and feed the results back
        -> repeat until it stops asking for tools (or we hit the turn budget)

Around that core sit the pieces that make it usable: context compression before
each request, a consent check and shell hooks around each tool call, read-only
tools run in parallel, and a todo list injected back into the conversation so the
model always sees the current plan.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import Config
from ..context import ContextManager
from ..hooks import Hooks
from ..llm import LLM
from ..permissions import Permission
from ..prompt import PLAN_MODE_PROMPT, system_prompt
from ..providers.base import ChatProvider
from ..session import Session
from ..skills import SkillCatalog, default_roots
from ..taskplan import TaskPlan
from ..tools import Registry, build_registry
from ..tools.agent import AgentTool
from ..tools.skills import LoadSkillTool
from ..tools.taskplan import task_plan_tools
from .execution import ToolExecutor

DeltaCallback = Callable[[str], None]
ToolCallback = Callable[[str, dict[str, Any]], None]
ResultCallback = Callable[[str, str], None]

# Sent at most once per turn when the model tries to stop with an unfinished plan.
PLAN_RECONCILE = (
    "Your task plan still has unfinished items. Keep working on them, or mark each "
    "one done or blocked if it no longer applies."
)


class Agent:
    """Runs the model<->tools loop for a single session."""

    def __init__(
        self,
        config: Config,
        session: Session,
        registry: Registry,
        llm: ChatProvider | None = None,
        *,
        permissions: Permission | None = None,
        hooks: Hooks | None = None,
        context: ContextManager | None = None,
        skills: SkillCatalog | None = None,
        plan: TaskPlan | None = None,
        on_delta: DeltaCallback | None = None,
        on_reasoning: DeltaCallback | None = None,
        on_tool: ToolCallback | None = None,
        on_tool_result: ResultCallback | None = None,
        allow_subagents: bool = True,
    ) -> None:
        self.config = config
        self.session = session
        self.registry = registry
        self.llm = llm or LLM(config)
        # Default: nothing to grant consent, so mutating tools are refused.
        self.permissions = permissions or Permission(workspace=str(config.workspace.resolve()))
        self.hooks = hooks or Hooks([], [])
        self.context = context or ContextManager(config.max_context_tokens)
        self.skills = skills if skills is not None else SkillCatalog(default_roots(config.workspace))
        self.plan = plan if plan is not None else (
            TaskPlan.from_snapshot(session.plan_snapshot) if session.plan_snapshot is not None else TaskPlan()
        )
        self.plan_mode = False

        self.on_delta = on_delta or (lambda _text: None)
        self.on_reasoning = on_reasoning or (lambda _text: None)
        self.on_tool = on_tool or (lambda _name, _args: None)
        self.on_tool_result = on_tool_result or (lambda _name, _text: None)

        # Sub-agents must not be able to spawn further sub-agents.
        if allow_subagents:
            self.registry.register(AgentTool(self._spawn_subagent))
        if self.skills:
            self.registry.register(LoadSkillTool(self.skills))
        for tool in task_plan_tools(self.plan):
            self.registry.register(tool)
        self.executor = ToolExecutor(
            registry=self.registry,
            permissions=self.permissions,
            hooks=self.hooks,
            session=self.session,
            plan=self.plan,
            on_tool=self.on_tool,
            on_tool_result=self.on_tool_result,
        )

    # -- public ------------------------------------------------------------

    def run(self, user_input: str) -> str:
        """Handle one user message and return the agent's final text reply."""
        self.session.record({"role": "user", "content": user_input})

        final_text = ""
        nudged = False
        turns = 0
        while turns < self.config.max_turns:
            turns += 1
            assistant = self._model_turn()
            if not assistant:
                break

            self.session.record(assistant)
            final_text = assistant.get("content") or final_text

            tool_calls = assistant.get("tool_calls")
            if tool_calls:
                self.executor.run(tool_calls, plan_mode=self.plan_mode)
                self.session.save()
                continue

            # The model wants to stop. Nudge once if the plan is unfinished.
            if not nudged and self.plan.unfinished():
                nudged = True
                self.session.record({"role": "user", "content": PLAN_RECONCILE})
                continue
            break
        else:
            final_text = (final_text + f"\n\n[stopped: reached the {self.config.max_turns}-turn limit]").strip()

        self.session.save()
        return final_text

    # -- model turn --------------------------------------------------------

    def _model_turn(self) -> dict[str, Any] | None:
        messages = self._outgoing_messages()
        assistant: dict[str, Any] | None = None
        for event in self.llm.stream(messages, self.registry.schemas()):
            if event[0] == "delta":
                self.on_delta(event[1])
            elif event[0] == "reasoning":
                self.on_reasoning(event[1])
            else:
                assistant = event[1]
        return assistant

    def _outgoing_messages(self) -> list[dict[str, Any]]:
        # Shrink the conversation first so we never send an oversized request.
        # If compression changed the projection, record it as a checkpoint.
        if self.context.maybe_compress(self.session.messages, self.llm):
            self.session.checkpoint(self.session.messages)

        system = system_prompt(self.registry)
        skills = self._skills_block()
        if skills:
            system += "\n\n" + skills
        if self.plan_mode:
            system += PLAN_MODE_PROMPT

        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        todo = self._todo_block()
        if todo:
            messages.append({"role": "system", "content": todo})
        plan = self._plan_block()
        if plan:
            messages.append({"role": "system", "content": plan})
        messages.extend(self.session.messages)
        return messages

    def _plan_block(self) -> str:
        if not self.plan.tasks:
            return ""
        return "# Task plan\n" + self.plan.describe()

    def _skills_block(self) -> str:
        if not self.skills:
            return ""
        return "# Skills\nCall load_skill(name) to load a skill's full instructions.\n" + self.skills.render()

    def _todo_block(self) -> str:
        tool = self.registry.get("todo_write")
        rendered = getattr(tool, "render", lambda: "")()
        return f"# Current task list\n{rendered}" if rendered and rendered != "(no tasks)" else ""

    # -- sub-agents --------------------------------------------------------

    def _spawn_subagent(self, task: str) -> str:
        """Run a child agent on a self-contained task and return its report."""
        child_registry = build_registry(self.config.workspace)  # no browser, no sub-agents
        child_session = Session.new(self.config.model, str(self.config.workspace))
        child = Agent(
            self.config,
            child_session,
            child_registry,
            llm=self.llm,
            permissions=self.permissions,
            hooks=self.hooks,
            allow_subagents=False,
        )
        return child.run(task)

