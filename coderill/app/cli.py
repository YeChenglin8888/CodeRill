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
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import choice
from prompt_toolkit.styles import Style
from rich.console import Group
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import __version__, checkpoints
from ..config import Config
from ..language import is_chinese
from ..permissions import Mode
from ..providers.openai_compatible import Usage
from ..session import SESSION_DIR, Session
from .factory import build_state
from .state import State, _short, _write

HISTORY_FILE = SESSION_DIR.parent / "history"
BRAND_MARK = "≋"

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

HELP_TEXT_ZH = """\
命令（命令名保持英文）：
  /help                查看帮助
  /new                 新建会话
  /list                列出已保存的会话
  /resume <id>         恢复会话（ID 可只输入前缀）
  /where               查看工作目录和会话 ID
  /share               输出隐去敏感信息的会话记录
  /fork                从当前会话创建分支
  /cost                查看用量和预估费用
  /undo                撤销上一次文件修改
  /plan [on|off]       切换计划模式（只读）
  /approve [on|off]    切换自动批准所有工具调用
  /mode <mode>         权限模式：standard | aggressive | bypass
  /reasoning [on|off]  切换推理过程显示
  /mcp                 列出已加载的 MCP 工具
  /attach <path>       为下一条消息附加文件或图片
  /model <profile>     切换模型配置
  /background          查看后台任务结果
  /apply <child-id>    查看并应用子任务修改
  /exit, /quit         退出
"""


def _approval_choice(chinese: bool) -> str:
    options = ([
        ("approve", "仅批准这一次：执行当前操作"),
        ("always", "始终允许同类操作：保存授权并继续"),
        ("deny", "拒绝：不执行当前操作"),
    ] if chinese else [
        ("approve", "Approve once: run this operation"),
        ("always", "Always allow similar operations: save a grant"),
        ("deny", "Deny: skip this operation"),
    ])
    return choice("请选择权限决定（方向键选择，回车确认）" if chinese else
                  "Choose a permission decision (arrow keys, Enter):",
                  options=options, default="deny")


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


def _print_sessions(console: Console, chinese: bool = False) -> None:
    sessions = Session.list_all()
    if not sessions:
        console.print("[dim]暂无保存的会话[/dim]" if chinese else "[dim]no saved sessions yet[/dim]")
        return
    for session in sessions:
        console.print(f"[cyan]{session.id}[/cyan]  [dim]{session.updated}  {session.turns} "
                      f"{'轮' if chinese else 'turn(s)'}  {session.model}[/dim]")


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

        _print_welcome(state, console)
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
                chinese = is_chinese(state.session.messages)
                console.print(f"\n[yellow]{'请求执行' if chinese else 'Allow'}[/yellow] "
                              f"{review['call']['name']}? [dim]{review['call']['arguments']}[/dim]")
                if review["diff"]:
                    console.print(review["diff"], style="dim")
                if not sys.stdin.isatty():
                    console.print(f"[yellow]{'等待批准' if chinese else 'Approval pending'}: {review['id']}[/yellow]")
                    return 2
                decision = _approval_choice(chinese)
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


def _print_welcome(state: State, console: Console) -> None:
    zh = is_chinese(state.session.messages)
    left = Table.grid(expand=True)
    left.add_column(justify="center")
    left.add_row(Text("欢迎回来" if zh and state.session.turns else
                      "欢迎使用" if zh else "Welcome back" if state.session.turns else "Welcome",
                      style="bold"))
    left.add_row(Text(f" {BRAND_MARK} ", style="bold #c5d7c7 on #3e5549"))
    left.add_row(Text(str(state.config.model), style="dim"))
    left.add_row(Text(_short(state.config.workspace, 42), style="dim"))

    right = Table.grid(expand=True)
    right.add_column()
    right.add_row(Text("开始使用" if zh else "Get started", style="bold #789183"))
    right.add_row(Text("直接描述任务，或输入 /help 查看命令" if zh else
                       "Describe a task, or type /help for commands"))
    right.add_row(Text("/attach 添加文件 · /plan 只读规划" if zh else
                       "/attach add a file · /plan plan without changes"))
    right.add_row(Text("会话与权限" if zh else "Sessions and permissions", style="bold #789183"))
    right.add_row(Text("/resume 恢复会话 · /mode 权限选项" if zh else
                       "/resume continue · /mode permissions"))

    if console.width >= 80:
        columns = Table.grid(expand=True, padding=(0, 2))
        columns.add_column(width=30)
        columns.add_column(ratio=1)
        columns.add_row(left, right)
        content = columns
    else:
        content = Group(left, Text(""), right)
    console.print(Panel(content, title=f"CodeRill · v{__version__}", border_style="#789183", padding=(1, 2)))
    console.print()


def _repl(state: State, console: Console) -> int:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    prompt_session: PromptSession[str] = PromptSession(history=FileHistory(str(HISTORY_FILE)))
    # Let prompt_toolkit size the framed editor to its content instead of filling the terminal.
    prompt_session.app.layout.current_window.dont_extend_height = lambda: True
    keys = KeyBindings()
    editor_style = Style.from_dict({"frame.border": "#789183", "bottom-toolbar": "#789183"})

    @keys.add("enter")
    def _send(event):
        event.current_buffer.validate_and_handle()

    @keys.add("c-j")
    @keys.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    while True:
        try:
            zh = is_chinese(state.session.messages)
            text = prompt_session.prompt(
                "",
                multiline=True,
                show_frame=True,
                style=editor_style,
                prompt_continuation="   │ ",
                key_bindings=keys,
                placeholder="描述任务，或输入 /help 查看命令…" if zh else "Describe a task, or type /help for commands…",
                bottom_toolbar=(" Enter 发送 · Ctrl+J / Alt+Enter 换行 · ↑ 历史"
                                if zh else " Enter send · Ctrl+J / Alt+Enter newline · ↑ history"),
            ).strip()
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
    zh = is_chinese(state.session.messages)

    if command in ("/exit", "/quit"):
        return True
    if command == "/help":
        console.print(HELP_TEXT_ZH if zh else HELP_TEXT)
    elif command == "/where":
        console.print(f"{'工作目录' if zh else 'workspace'}: [cyan]{config.workspace}[/cyan]\n"
                      f"{'会话' if zh else 'session'}: [cyan]{state.session.id}[/cyan]")
    elif command == "/list":
        _print_sessions(console, zh)
    elif command == "/new":
        state.session = Session.new(config.model, str(config.workspace))
        state.rebuild()
        console.print(f"[green]{'已新建会话' if zh else 'Started session'} {state.session.id}[/green]")
    elif command == "/resume":
        loaded = _find_session(rest) if rest else None
        if loaded is None:
            console.print(f"[red]{'未找到会话' if zh else 'No session matching'} '{rest}'[/red]")
        else:
            state.session = loaded
            state.rebuild()
            console.print(f"[green]{'已恢复会话' if zh else 'Resumed session'} {state.session.id}[/green]")
    elif command == "/cost":
        _print_cost(state, console)
    elif command == "/share":
        console.print(state.session.transcript())
    elif command == "/fork":
        state.session = state.session.fork()
        state.rebuild()
        console.print(f"[green]{'已创建分支会话' if zh else 'Forked into session'} {state.session.id}[/green]")
    elif command == "/undo":
        console.print(checkpoints.undo())
    elif command == "/plan":
        state.plan_mode = _toggle(rest, state.plan_mode)
        state.agent.plan_mode = state.plan_mode
        console.print(f"{'计划模式' if zh else 'plan mode'}: {_status(state.plan_mode, zh)}")
    elif command == "/approve":
        state.permission.allow_all = _toggle(rest, state.permission.allow_all)
        console.print(f"{'自动批准' if zh else 'auto-approve'}: {_status(state.permission.allow_all, zh)}")
    elif command == "/mode":
        if not rest and sys.stdin.isatty():
            rest = choice("选择权限模式：" if zh else "Choose permission mode:", options=[
                (Mode.STANDARD.value, "标准：每次询问" if zh else "Standard: ask before protected actions"),
                (Mode.AGGRESSIVE.value, "积极：自动批准文件和命令操作" if zh else
                 "Aggressive: auto-approve file and shell actions"),
                (Mode.BYPASS.value, "跳过：自动批准所有操作" if zh else "Bypass: auto-approve all actions"),
            ], default=state.permission.mode.value)
        if rest in {mode.value for mode in Mode}:
            state.permission.mode = Mode(rest)
        console.print(f"{'权限模式' if zh else 'permission mode'}: [cyan]{state.permission.mode.value}[/cyan]")
    elif command == "/reasoning":
        state.show_reasoning = _toggle(rest, state.show_reasoning)
        console.print(f"{'推理过程' if zh else 'reasoning'}: {_status(state.show_reasoning, zh)}")
    elif command == "/mcp":
        _print_mcp(state, console)
    elif command == "/attach":
        try:
            attachment = state.session.attach_file(rest)
            state.pending_attachments.append(attachment)
            console.print(f"[green]{'已附加到下一条消息' if zh else 'Attached to next message'}: "
                          f"{attachment['name']}[/green]")
        except (OSError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
    elif command == "/model":
        try:
            if not rest and sys.stdin.isatty() and state.config.profiles:
                rest = choice("选择模型配置：" if zh else "Choose a model profile:",
                              options=[(name, f"{name} · {profile.get('provider', state.config.provider)}/"
                                             f"{profile.get('model', state.config.model)}")
                                       for name, profile in state.config.profiles.items()])
            state.select_model(rest)
            console.print(f"[green]{'模型' if zh else 'Model'}: {state.config.provider}/{state.config.model}[/green]")
        except (RuntimeError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
    elif command == "/background":
        service = state.subagent_services[state.session.id]
        for item in service.background.notifications():
            console.print(f"[green]{'已完成' if zh else 'Completed'} {item['id']}[/green] {item['result']}")
        for item in state.session.background_tasks.values():
            console.print(f"{item['id']}  {item['status']}  {item['description']}")
    elif command == "/apply":
        service = state.subagent_services[state.session.id]
        try:
            diff = state.run_async(service.preview_apply(rest))
            if not diff:
                console.print("[dim]没有待应用的修改[/dim]" if zh else "[dim]Nothing to apply[/dim]")
            else:
                console.print(diff)
                if choice("将这些修改应用到主工作目录？" if zh else "Apply these changes to the main workspace?",
                          options=[(False, "取消" if zh else "Cancel"),
                                   (True, "应用修改" if zh else "Apply changes")], default=False):
                    console.print(state.run_async(service.apply(rest, diff)))
        except (KeyError, RuntimeError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
    else:
        console.print(f"[red]{'未知命令' if zh else 'Unknown command'}: {command}[/red]")
    return False


def _toggle(rest: str, current: bool) -> bool:
    if rest in ("on", "1", "true"):
        return True
    if rest in ("off", "0", "false"):
        return False
    return not current


def _status(enabled: bool, chinese: bool) -> str:
    if enabled:
        return "[green]开启[/green]" if chinese else "[green]on[/green]"
    return "[dim]关闭[/dim]" if chinese else "[dim]off[/dim]"


def _print_cost(state: State, console: Console) -> None:
    usage = Usage(**state.session.usage)
    cost = usage.cost(state.config.model)
    zh = is_chinese(state.session.messages)
    money = f"${cost:.4f}" if cost is not None else (
        "未知（没有该模型的价格信息）" if zh else "unknown (model has no price table entry)")
    console.print(
        f"{'请求数' if zh else 'requests'}: [cyan]{usage.requests}[/cyan]   "
        f"{'输入' if zh else 'input'}: [cyan]{usage.input_tokens}[/cyan]   "
        f"{'输出' if zh else 'output'}: [cyan]{usage.output_tokens}[/cyan]   "
        f"{'预估费用' if zh else 'est. cost'}: [cyan]{money}[/cyan]"
    )


def _print_mcp(state: State, console: Console) -> None:
    state.run_async(state.mcp.connect())
    if not state.mcp_tools:
        console.print("[dim]未加载 MCP 工具（检查 ~/.coderill/mcp.json）[/dim]" if
                      is_chinese(state.session.messages) else
                      "[dim]no MCP tools loaded (see ~/.coderill/mcp.json)[/dim]")
        return
    for tool in state.mcp_tools:
        console.print(f"[cyan]{tool.name}[/cyan]  [dim]{_short(tool.description, 80)}[/dim]")
