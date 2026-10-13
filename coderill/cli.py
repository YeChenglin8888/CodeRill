"""The command-line interface.

Two ways to use it:

    coderill                          # interactive REPL
    coderill "fix the failing test"   # one-shot: run a single request and exit

Inside the REPL, slash commands manage sessions, cost, undo, permissions, plan
mode and MCP. Everything else you type is sent to the agent.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.console import Console

from . import __version__, checkpoints
from .agent import Agent
from .config import Config
from .context import ContextManager
from .hooks import Hooks, load_hooks
from .llm import LLM, LLMError
from .mcp import load_mcp_tools
from .permissions import Mode, Permission
from .review import build_prewrite_review
from .session import SESSION_DIR, Session
from .tools import Registry, build_registry

HISTORY_FILE = SESSION_DIR.parent / "history"

HELP_TEXT = """\
Commands:
  /help                show this help
  /new                 start a fresh session
  /list                list saved sessions
  /resume <id>         resume a saved session (id may be a prefix)
  /where               show the workspace and current session id
  /share               print a redacted transcript of this session
  /fork                branch this session into a new one
  /cost                show token usage and estimated cost
  /undo                undo the last file change
  /plan [on|off]       toggle plan mode (read-only investigation)
  /approve [on|off]    toggle auto-approving every tool call
  /mode <mode>         set the permission mode: standard | aggressive | bypass
  /reasoning [on|off]  toggle showing the model's reasoning stream
  /mcp                 list tools loaded from MCP servers
  /exit, /quit         leave
"""


@dataclass
class State:
    """Everything the REPL needs, so sessions can be swapped in place."""

    config: Config
    session: Session
    console: Console
    llm: LLM
    permission: Permission
    hooks: Hooks
    mcp_tools: list = field(default_factory=list)
    agent: Agent | None = None
    show_reasoning: bool = False
    plan_mode: bool = False

    def rebuild(self) -> None:
        registry: Registry = build_registry(
            self.config.workspace, llm=self.llm, config=self.config, extra_tools=self.mcp_tools
        )
        self.agent = Agent(
            self.config,
            self.session,
            registry,
            llm=self.llm,
            permissions=self.permission,
            hooks=self.hooks,
            context=ContextManager(self.config.max_context_tokens),
            on_delta=_write,
            on_reasoning=self._on_reasoning,
            on_tool=self._on_tool,
            on_tool_result=self._on_tool_result,
        )
        self.agent.plan_mode = self.plan_mode

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


# -- argument parsing -------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="coderill", description="A minimal AI coding agent.")
    parser.add_argument("prompt", nargs="*", help="Optional one-shot request. Omit to open the REPL.")
    parser.add_argument("--cwd", default=None, help="Workspace directory. Defaults to the current directory.")
    parser.add_argument("--model", default=None, help="Override the model for this run.")
    parser.add_argument("--resume", default=None, help="Resume a saved session by id (prefix ok).")
    parser.add_argument("--list", action="store_true", help="List saved sessions and exit.")
    parser.add_argument("--plan", action="store_true", help="Start in plan mode (read-only).")
    parser.add_argument(
        "--auto-approve", action="store_true", help="Run every tool without asking for consent."
    )
    parser.add_argument("--version", action="version", version=f"coderill {__version__}")
    return parser.parse_args(argv)


def _find_session(prefix: str) -> Session | None:
    """Resolve a session by id, a .jsonl path, or a unique id prefix."""
    candidate = Path(prefix)
    if candidate.exists() and candidate.suffix == ".jsonl":
        prefix = candidate.stem
    try:
        return Session.load(prefix)
    except (FileNotFoundError, ValueError):
        pass

    matches = [s for s in Session.list_all() if s.id.startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SystemExit(f"'{prefix}' matches {len(matches)} sessions; be more specific.")
    return None


def _print_sessions(console: Console) -> None:
    sessions = Session.list_all()
    if not sessions:
        console.print("[dim]no saved sessions yet[/dim]")
        return
    for session in sessions:
        console.print(f"[cyan]{session.id}[/cyan]  [dim]{session.updated}  {session.turns} turn(s)  {session.model}[/dim]")


# -- wiring -----------------------------------------------------------------


def make_asker(console: Console, root_getter):
    """Build the interactive consent prompt, showing a diff for file writes."""

    def ask(tool_name: str, arguments: dict):
        preview = _short(json.dumps(arguments, ensure_ascii=False), 300)
        console.print(f"\n[yellow]allow[/yellow] {tool_name}? [dim]{preview}[/dim]")
        review = build_prewrite_review(tool_name, arguments, root_getter())
        if review:
            console.print(review, style="dim")
        answer = console.input("  [y]es / [n]o / [a]lways > ").strip().lower()
        if answer == "a":
            return "always"
        return answer.startswith("y")

    return ask


def build_state(config: Config, session: Session, console: Console, *, auto_approve: bool, plan: bool) -> State:
    root = config.workspace
    permission = Permission(
        ask=None if auto_approve else make_asker(console, lambda: root),
        allow_all=auto_approve,
    )

    state = State(
        config=config,
        session=session,
        console=console,
        llm=LLM(config),
        permission=permission,
        hooks=load_hooks(),
        mcp_tools=_safe_mcp_tools(),
        plan_mode=plan,
    )
    state.rebuild()
    return state


def _safe_mcp_tools() -> list:
    try:
        return load_mcp_tools()
    except Exception:  # noqa: BLE001 - a bad MCP setup must not stop the CLI
        return []


# -- entry points -----------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    console = Console()

    if args.list:
        _print_sessions(console)
        return 0

    try:
        config = Config.from_env(workspace=args.cwd)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    if args.model:
        config.model = args.model

    if args.resume:
        session = _find_session(args.resume)
        if session is None:
            console.print(f"[red]no session matching '{args.resume}'[/red]")
            return 1
    else:
        session = Session.new(config.model, str(config.workspace))

    state = build_state(config, session, console, auto_approve=args.auto_approve, plan=args.plan)

    if args.prompt:
        return _run_once(state, " ".join(args.prompt), console)

    console.print(f"[bold]CodeRill[/bold] [dim]{__version__}[/dim]  workspace: [cyan]{config.workspace}[/cyan]")
    console.print(f"[dim]model: {config.model}   session: {session.id}   /help for commands[/dim]\n")
    return _repl(state, console)


def _run_once(state: State, prompt: str, console: Console) -> int:
    try:
        state.agent.run(prompt)
    except LLMError as exc:
        console.print(f"\n[red]{exc}[/red]")
        return 1
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/dim]")
        return 130
    _write("\n")
    return 0


def _repl(state: State, console: Console) -> int:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    prompt_session: PromptSession[str] = PromptSession(history=FileHistory(str(HISTORY_FILE)))

    while True:
        try:
            text = prompt_session.prompt("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0
        if not text:
            continue

        if text.startswith("/"):
            if _handle_command(state, console, text):
                return 0
            continue

        try:
            state.agent.run(text)
        except LLMError as exc:
            console.print(f"\n[red]{exc}[/red]")
        except KeyboardInterrupt:
            console.print("\n[dim]interrupted[/dim]")
        _write("\n")


def _handle_command(state: State, console: Console, text: str) -> bool:
    """Run a slash command. Returns True to exit the REPL."""
    command, _, rest = text.partition(" ")
    rest = rest.strip()
    config = state.config

    if command in ("/exit", "/quit"):
        return True
    if command == "/help":
        console.print(HELP_TEXT)
    elif command == "/where":
        console.print(f"workspace: [cyan]{config.workspace}[/cyan]\nsession:   [cyan]{state.session.id}[/cyan]")
    elif command == "/list":
        _print_sessions(console)
    elif command == "/new":
        state.session = Session.new(config.model, str(config.workspace))
        state.rebuild()
        console.print(f"[green]started session {state.session.id}[/green]")
    elif command == "/resume":
        loaded = _find_session(rest) if rest else None
        if loaded is None:
            console.print(f"[red]no session matching '{rest}'[/red]")
        else:
            state.session = loaded
            state.rebuild()
            console.print(f"[green]resumed session {state.session.id}[/green]")
    elif command == "/cost":
        _print_cost(state, console)
    elif command == "/share":
        console.print(state.session.transcript())
    elif command == "/fork":
        state.session = state.session.fork()
        state.rebuild()
        console.print(f"[green]forked into session {state.session.id}[/green]")
    elif command == "/undo":
        console.print(checkpoints.undo())
    elif command == "/plan":
        state.plan_mode = _toggle(rest, state.plan_mode)
        state.agent.plan_mode = state.plan_mode
        console.print(f"plan mode: {'[green]on[/green]' if state.plan_mode else '[dim]off[/dim]'}")
    elif command == "/approve":
        state.permission.allow_all = _toggle(rest, state.permission.allow_all)
        console.print(f"auto-approve: {'[green]on[/green]' if state.permission.allow_all else '[dim]off[/dim]'}")
    elif command == "/mode":
        if rest in {mode.value for mode in Mode}:
            state.permission.mode = Mode(rest)
        console.print(f"permission mode: [cyan]{state.permission.mode.value}[/cyan]")
    elif command == "/reasoning":
        state.show_reasoning = _toggle(rest, state.show_reasoning)
        console.print(f"reasoning: {'[green]on[/green]' if state.show_reasoning else '[dim]off[/dim]'}")
    elif command == "/mcp":
        _print_mcp(state, console)
    else:
        console.print(f"[red]unknown command: {command}[/red]")
    return False


def _toggle(rest: str, current: bool) -> bool:
    if rest in ("on", "1", "true"):
        return True
    if rest in ("off", "0", "false"):
        return False
    return not current


def _print_cost(state: State, console: Console) -> None:
    usage = state.llm.usage
    cost = usage.cost(state.config.model)
    money = f"${cost:.4f}" if cost is not None else "unknown (model has no price table entry)"
    console.print(
        f"requests: [cyan]{usage.requests}[/cyan]   "
        f"input: [cyan]{usage.input_tokens}[/cyan]   "
        f"output: [cyan]{usage.output_tokens}[/cyan]   "
        f"est. cost: [cyan]{money}[/cyan]"
    )


def _print_mcp(state: State, console: Console) -> None:
    if not state.mcp_tools:
        console.print("[dim]no MCP tools loaded (see ~/.coderill/mcp.json)[/dim]")
        return
    for tool in state.mcp_tools:
        console.print(f"[cyan]{tool.name}[/cyan]  [dim]{_short(tool.description, 80)}[/dim]")
