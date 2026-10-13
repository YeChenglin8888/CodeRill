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

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .config import Config
from .context import ContextManager
from .hooks import Hooks
from .llm import LLM
from .permissions import Permission
from .prompt import PLAN_MODE_PROMPT, system_prompt
from .session import Session
from .skills import SkillCatalog, default_roots
from .taskplan import TaskPlan
from .tools import Registry, build_registry
from .tools.agent import AgentTool
from .tools.skills import LoadSkillTool
from .tools.taskplan import task_plan_tools

DeltaCallback = Callable[[str], None]
ToolCallback = Callable[[str, dict[str, Any]], None]
ResultCallback = Callable[[str, str], None]

MAX_PARALLEL = 4

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
        llm: LLM | None = None,
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
        self.permissions = permissions or Permission()
        self.hooks = hooks or Hooks([], [])
        self.context = context or ContextManager(config.max_context_tokens)
        self.skills = skills if skills is not None else SkillCatalog(default_roots(config.workspace))
        self.plan = plan or TaskPlan()
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
                self._run_tool_calls(tool_calls)
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

    # -- tool execution ----------------------------------------------------

    def _run_tool_calls(self, tool_calls: list[dict[str, Any]]) -> None:
        results: list[str | None] = [None] * len(tool_calls)
        pending: list[tuple[int, str, dict[str, Any]]] = []

        # Phase 1: hooks and consent, sequentially, since they may prompt.
        for index, call in enumerate(tool_calls):
            name, arguments = _unpack(call)
            self.on_tool(name, arguments)
            refusal = self._veto(name, arguments)
            if refusal is not None:
                results[index] = refusal
            else:
                pending.append((index, name, arguments))

        # Phase 2: run approved calls -- read-only ones in parallel, the rest serially.
        parallel = [item for item in pending if self._is_read_only(item[1])]
        serial = [item for item in pending if not self._is_read_only(item[1])]
        if parallel:
            with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(parallel))) as pool:
                futures = {pool.submit(self._dispatch, name, arguments): index for index, name, arguments in parallel}
                for future, index in futures.items():
                    results[index] = future.result()
        for index, name, arguments in serial:
            results[index] = self._dispatch(name, arguments)

        # Phase 3: append one tool result per call, in order.
        for index, call in enumerate(tool_calls):
            name, _ = _unpack(call)
            content = results[index] or ""
            self.on_tool_result(name, content)
            self.session.record({"role": "tool", "tool_call_id": call.get("id", ""), "content": content})

    def _veto(self, name: str, arguments: dict[str, Any]) -> str | None:
        """Return a refusal string if this call must not run, else None."""
        tool = self.registry.get(name)
        if tool is None:
            return None  # let the dispatcher report the unknown tool

        if self.plan_mode and not tool.read_only:
            return "refused: plan mode is on; only read-only tools may run"

        reason = self.hooks.run_pre(name, arguments)
        if reason is not None:
            return f"refused by pre hook: {reason}"

        return self.permissions.check(tool, arguments)

    def _dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        result = self.registry.dispatch(name, arguments)
        self.hooks.run_post(name, arguments, result)
        return result

    def _is_read_only(self, name: str) -> bool:
        tool = self.registry.get(name)
        return bool(tool and tool.read_only)

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


def _unpack(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    function = call.get("function", {})
    return function.get("name", ""), _parse_arguments(function.get("arguments", ""))


def _parse_arguments(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
