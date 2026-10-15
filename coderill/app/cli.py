"""The command-line interface.

Two ways to use it:

    coderill                          # interactive REPL
    coderill "fix the failing test"   # one-shot: run a single request and exit

Inside the REPL, slash commands manage sessions, cost, undo, permissions, plan
mode and MCP. Everything else you type is sent to the agent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.console import Console

from .. import __version__, checkpoints
from ..config import Config
from ..llm import LLM
from ..permissions import Mode
from ..providers.openai_compatible import Usage
from ..session import SESSION_DIR, Session
from .factory import build_state
from .state import State, _short, _write

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
  /attach <path>       add a text file or image to the next message
  /model <profile>     switch to a configured model profile
  /background          list and collect background task results
  /apply <child-id>    review and apply a child worktree diff
  /exit, /quit         leave
"""


# -- argument parsing -------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="coderill", description="A minimal AI coding agent.")
    parser.add_argument("prompt", nargs="*", help="Optional one-shot request. Omit to open the REPL.")
    parser.add_argument("--cwd", default=None, help="Workspace directory. Defaults to the current directory.")
    parser.add_argument("--model", default=None, help="Override the model for this run.")
    parser.add_argument("--provider", choices=("openai", "anthropic"), help="Model provider.")
    parser.add_argument("--message", default=None, help="Run one request and exit.")
    parser.add_argument("--tui", action="store_true", help="Open the optional Textual interface.")
    parser.add_argument("--attach", action="append", default=[], help="Attach a text file or image to the message.")
    parser.add_argument("--resume", default=None, help="Resume a saved session by id (prefix ok).")
    parser.add_argument("--list", action="store_true", help="List saved sessions and exit.")
    parser.add_argument("--plan", action="store_true", help="Start in plan mode (read-only).")
    parser.add_argument("--benchmark", action="store_true", help="Require execution evidence before ending a non-interactive task.")
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


# -- entry points -----------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    console = Console()

    if args.list:
        _print_sessions(console)
        return 0

    try:
        config = Config.from_env(workspace=args.cwd, overrides={"model": args.model, "provider": args.provider,
                                                                "benchmark": args.benchmark})
    except (RuntimeError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    if args.resume:
        session = _find_session(args.resume)
        if session is None:
            console.print(f"[red]no session matching '{args.resume}'[/red]")
            return 1
    else:
        session = Session.new(config.model, str(config.workspace))

    state = build_state(config, session, console, auto_approve=args.auto_approve, plan=args.plan)
    try:
        state.pending_attachments.extend(session.attach_file(path) for path in args.attach)
    except (OSError, ValueError) as exc:
        console.print(f"[red]attachment: {exc}[/red]")
        state.close()
        return 1

    try:
        if args.tui:
            try:
                from .tui import run_tui
            except ImportError:
                console.print("[red]TUI requires the optional dependency: pip install 'coderill[tui]'[/red]")
                return 1

            return run_tui(state)
        if args.message or args.prompt:
            return _run_once(state, args.message or " ".join(args.prompt), console)

        console.print(f"[bold]CodeRill[/bold] [dim]{__version__}[/dim]  workspace: [cyan]{config.workspace}[/cyan]")
        console.print(f"[dim]model: {config.model}   session: {session.id}   /help for commands[/dim]\n")
        return _repl(state, console)
    finally:
        state.close()


def _run_once(state: State, prompt: str, console: Console) -> int:
    try:
        prior = state.session.pending_review or state.session.incomplete_calls or state.session.last_status == "running"
        result = state.run_async(state.agent.resume_turn()) if prior else None
        submitted = False
        while True:
            if result is None:
                result = state.run_async(state.agent.run_turn(prompt, state.pending_attachments))
                submitted = True
                if result.status != "failed":
                    state.pending_attachments.clear()
            if result.status == "waiting":
                review = result.review
                console.print(f"\n[yellow]allow[/yellow] {review['call']['name']}? [dim]{review['call']['arguments']}[/dim]")
                if review["diff"]:
                    console.print(review["diff"], style="dim")
                if not sys.stdin.isatty():
                    console.print(f"[yellow]approval pending: {review['id']}[/yellow]")
                    return 2
                answer = console.input("  [y]es / [n]o / [a]lways > ").strip().lower()
                decision = "always" if answer == "a" else "approve" if answer.startswith("y") else "deny"
                result = state.run_async(state.agent.resume_turn(review["id"], decision))
                continue
            if result.status != "complete":
                console.print(f"\n[red]{result.error or result.status}[/red]")
                return 1
            if submitted:
                break
            result = None
    except KeyboardInterrupt:
        state.agent.cancel_turn()
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

        _run_once(state, text, console)


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
    elif command == "/attach":
        try:
            attachment = state.session.attach_file(rest)
            state.pending_attachments.append(attachment)
            console.print(f"[green]attached {attachment['name']} to next message[/green]")
        except (OSError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
    elif command == "/model":
        try:
            config = Config.from_env(config.workspace, overrides={"profile": rest})
            state.config = config
            state.llm = LLM(config)
            state.session.set_model(config.model)
            state.rebuild()
            console.print(f"[green]model: {config.provider}/{config.model}[/green]")
        except (RuntimeError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
    elif command == "/background":
        service = state.subagent_services[state.session.id]
        for item in service.background.notifications():
            console.print(f"[green]completed {item['id']}[/green] {item['result']}")
        for item in state.session.background_tasks.values():
            console.print(f"{item['id']}  {item['status']}  {item['description']}")
    elif command == "/apply":
        service = state.subagent_services[state.session.id]
        try:
            diff = state.run_async(service.preview_apply(rest))
            if not diff:
                console.print("[dim]nothing to apply[/dim]")
            else:
                console.print(diff)
                if console.input("Apply this child diff to the main workspace? [y/N] ").strip().lower() == "y":
                    console.print(state.run_async(service.apply(rest, diff)))
        except (KeyError, RuntimeError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
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
    usage = Usage(**state.session.usage)
    cost = usage.cost(state.config.model)
    money = f"${cost:.4f}" if cost is not None else "unknown (model has no price table entry)"
    console.print(
        f"requests: [cyan]{usage.requests}[/cyan]   "
        f"input: [cyan]{usage.input_tokens}[/cyan]   "
        f"output: [cyan]{usage.output_tokens}[/cyan]   "
        f"est. cost: [cyan]{money}[/cyan]"
    )


def _print_mcp(state: State, console: Console) -> None:
    state.run_async(state.mcp.connect())
    if not state.mcp_tools:
        console.print("[dim]no MCP tools loaded (see ~/.coderill/mcp.json)[/dim]")
        return
    for tool in state.mcp_tools:
        console.print(f"[cyan]{tool.name}[/cyan]  [dim]{_short(tool.description, 80)}[/dim]")
