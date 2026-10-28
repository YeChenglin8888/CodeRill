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
import re
import shlex
import shutil
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import choice
from prompt_toolkit.styles import Style
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from .. import __version__, checkpoints
from ..config import Config
from ..language import is_chinese
from ..permissions import Mode
from ..providers.openai_compatible import Usage
from ..session import SESSION_DIR, Session
from .evolution import EvolutionSession, apply_active_harness
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
  /approve [on|off]    toggle bypass mode (legacy alias)
  /mode <mode>         standard | acceptEdits | plan | dontAsk | extreme (bypassPermissions)
  /reasoning [on|off]  toggle showing the model's reasoning stream
  /mcp                 list tools loaded from MCP servers
  /attach <path>       add a text file or image to the next message
  /model <profile>     switch to a configured model profile
  /background          list and collect background task results
  /apply <child-id>    review and apply a child worktree diff
  /harness             open the Harness² selection menu (F2)
  /track               save the workspace before a task
  /feedback <text>     record feedback on the tracked task
  /evolve <command>    refine Harness² using a verifier command
  /evaluate <file> [id] compare baseline and candidate on independent tasks
  /adopt               use a candidate that passed independent evaluation
  /evolution           show Harness² workflow status
  /memory [command]    list, read, correct, delete or index project memories
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
  /approve [on|off]    切换绕过模式（旧命令）
  /mode <模式>         standard | acceptEdits | plan | dontAsk | extreme（极限放权）
  /reasoning [on|off]  切换推理过程显示
  /mcp                 列出已加载的 MCP 工具
  /attach <path>       为下一条消息附加文件或图片
  /model <profile>     切换模型配置
  /background          查看后台任务结果
  /apply <child-id>    查看并应用子任务修改
  /harness             打开 Harness² 选择菜单（F2）
  /track               在任务前保存工作区快照
  /feedback <内容>     记录对已跟踪任务的反馈
  /evolve <验证命令>    基于验证反馈改进 Harness²
  /evaluate <清单> [ID] 在独立任务上对照评测
  /adopt               采纳通过独立评测的 Harness
  /evolution           查看进化流程状态
  /memory [命令]       查看、纠正、删除或索引项目记忆
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
    parser.add_argument("--permission-mode", choices=("default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"),
                        help="Set the starting permission mode.")
    parser.add_argument("--benchmark", action="store_true", help="Require execution evidence before ending a non-interactive task.")
    parser.add_argument("--sandbox", choices=("off", "docker"), help="Command sandbox backend.")
    parser.add_argument("--sandbox-profile", choices=("read-only", "workspace-write"), help="Sandbox file mode.")
    parser.add_argument("--sandbox-network", choices=("restricted", "none"), help="Sandbox network mode.")
    parser.add_argument("--sandbox-image", help="Local Docker execution image.")
    parser.add_argument("--sandbox-port", action="append", default=[], metavar="HOST:CONTAINER",
                        help="Publish one configured preview port on 127.0.0.1.")
    parser.add_argument(
        "--auto-approve", action="store_true", help="Skip ordinary tool consent; sandbox network grants still require review."
    )
    parser.add_argument("--version", action="version", version=f"coderill {__version__}")
    args = parser.parse_args(argv)
    if args.permission_mode and (args.plan or args.auto_approve):
        parser.error("--permission-mode cannot be combined with --plan or --auto-approve")
    return args


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
                                                                "benchmark": args.benchmark, "sandbox": args.sandbox,
                                                                "sandbox_profile": args.sandbox_profile,
                                                                "sandbox_network": args.sandbox_network,
                                                                "sandbox_image": args.sandbox_image,
                                                                "sandbox_ports": args.sandbox_port or None})
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
    if args.permission_mode:
        _set_mode(state, args.permission_mode)
    apply_active_harness(state, console)
    try:
        state.pending_attachments.extend(state.attach_file(path) for path in args.attach)
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
        result = _run_with_feedback(state, state.agent.resume_turn(), console) if prior else None
        submitted = False
        while True:
            if result is None:
                result = _run_with_feedback(state, state.agent.run_turn(prompt, state.pending_attachments), console)
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
                    if review.get("kind") == "sandbox_network":
                        result = _run_with_feedback(state, state.agent.resume_turn(review["id"], "deny"), console)
                        continue
                    console.print(f"[yellow]{'等待批准' if chinese else 'Approval pending'}: {review['id']}[/yellow]")
                    return 2
                decision = _approval_choice(chinese)
                result = _run_with_feedback(state, state.agent.resume_turn(review["id"], decision), console)
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


def _run_with_feedback(state: State, coroutine, console: Console):
    if not console.is_terminal or not hasattr(state.agent, "on_event"):
        return state.run_async(coroutine)
    original = state.agent.on_event
    status = console.status("思考中…" if is_chinese(state.session.messages) else "Thinking…", spinner="dots")
    active = True

    def on_event(event):
        nonlocal active
        if event.kind == "tool_started" and event.tool_call:
            status.update(("执行中" if is_chinese(state.session.messages) else "Running") +
                          f" {event.tool_call.name}…")
            if not active:
                status.start()
                active = True
        elif event.kind == "tool_result" and active:
            status.update("思考中…" if is_chinese(state.session.messages) else "Thinking…")
        elif event.kind == "text_delta" and active:
            status.stop()
            active = False
        original(event)

    state.agent.on_event = on_event
    try:
        with status:
            return state.run_async(coroutine)
    finally:
        state.agent.on_event = original


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
    left.add_row(Text(f"sandbox: {state.config.sandbox} / {state.config.sandbox_profile} / "
                      f"{state.config.sandbox_network}", style="dim"))

    right = Table.grid(expand=True)
    right.add_column()
    right.add_row(Text("开始使用" if zh else "Get started", style="bold #789183"))
    right.add_row(Text("直接描述任务，或输入 /help 查看命令" if zh else
                       "Describe a task, or type /help for commands"))
    right.add_row(Text("F2 Harness² 菜单 · /attach 添加文件" if zh else
                       "F2 Harness² menu · /attach add a file"))
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


def _active_mode(state: State) -> str:
    return "plan" if state.plan_mode else state.permission.mode.value


def _mode_label(state: State, zh: bool) -> str:
    return ("极限放权" if zh else "Full access") if _active_mode(state) == "bypass" else _active_mode(state)


def _set_mode(state: State, name: str) -> None:
    if name == "plan":
        state.plan_mode = state.agent.plan_mode = True
        return
    mode = Mode({"default": "standard", "bypassPermissions": "bypass", "extreme": "bypass"}.get(name, name))
    state.permission.mode = mode
    state.plan_mode = state.agent.plan_mode = False


def _repl(state: State, console: Console) -> int:
    evolution = EvolutionSession(state.config.workspace)
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    prompt_session: PromptSession[str] = PromptSession(history=FileHistory(str(HISTORY_FILE)))
    # Let prompt_toolkit size the framed editor to its content instead of filling the terminal.
    prompt_session.app.layout.current_window.dont_extend_height = lambda: True
    keys = KeyBindings()
    editor_style = Style.from_dict({"frame.border": "#789183", "bottom-toolbar": "#789183"})
    commands = tuple(dict.fromkeys(re.findall(r"/[a-z]+", HELP_TEXT)))

    @keys.add("enter")
    def _send(event):
        event.current_buffer.validate_and_handle()

    @keys.add("c-j")
    @keys.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    @keys.add("c-c")
    def _cancel_input(event):
        if event.current_buffer.text:
            event.current_buffer.reset()
        else:
            event.app.exit(exception=KeyboardInterrupt())

    @keys.add("s-tab")
    @keys.add("escape", "m")
    def _cycle_mode(event):
        modes = ("standard", "acceptEdits", "plan", "bypass")
        current = _active_mode(state)
        _set_mode(state, modes[(modes.index(current) + 1) % len(modes)] if current in modes else modes[0])
        event.app.invalidate()

    @keys.add("f2")
    def _harness_shortcut(event):
        if not event.current_buffer.text.strip():
            event.current_buffer.text = "/harness"
            event.current_buffer.validate_and_handle()

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
                completer=WordCompleter(commands, WORD=True),
                complete_while_typing=True,
                placeholder="描述任务，或输入 /help 查看命令…" if zh else "Describe a task, or type /help for commands…",
                bottom_toolbar=lambda zh=zh: (f" {state.config.model} · {_short(state.config.workspace, 28)} · "
                                        f"{_mode_label(state, zh)}  │  " +
                                        ("Enter 发送 · F2 Harness² 菜单 · Ctrl+J 换行 · Shift+Tab 权限"
                                         if zh else "Enter send · F2 Harness² menu · Ctrl+J newline · Shift+Tab mode")),
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0
        if not text:
            continue

        if text.startswith("/"):
            if _handle_command(state, console, text, evolution):
                return 0
            continue

        evolution.record_task(text, console)
        _run_once(state, text, console)


def _handle_command(state: State, console: Console, text: str,
                    evolution: EvolutionSession | None = None) -> bool:
    """Run a slash command. Returns True to exit the REPL."""
    command, _, rest = text.partition(" ")
    rest = rest.strip()
    config = state.config
    zh = is_chinese(state.session.messages)

    if command in ("/exit", "/quit"):
        return True
    if command == "/help":
        console.print(Text(HELP_TEXT_ZH if zh else HELP_TEXT))
    elif command == "/memory":
        memory = state.agent.memory
        action, _, argument = rest.partition(" ")
        try:
            if action in ("", "list"):
                console.print(Text(json.dumps(memory.list(), ensure_ascii=False, indent=2)))
            elif action == "read":
                console.print(Text(json.dumps(memory.get(argument), ensure_ascii=False, indent=2)))
            elif action == "delete":
                console.print("deleted" if memory.delete(argument) else "memory not found")
            elif action == "correct":
                item_id, _, content = argument.partition(" ")
                console.print(memory.correct(item_id, content) or "memory not found or invalid")
            elif action == "index":
                console.print(f"Indexed {memory.import_history(state.session.store)} new source records.")
            elif action == "setup":
                if shutil.disk_usage(Path.home()).free < 5 * 1024 ** 3:
                    raise ValueError("BGE-M3 setup needs at least 5 GiB free on the home drive")
                from sentence_transformers import SentenceTransformer
                SentenceTransformer("BAAI/bge-m3", device="cpu")
                memory._model_checked = False
                console.print("Local embedding model is ready.")
            else:
                console.print("/memory list | read ID | correct ID TEXT | delete ID | index | setup")
            if action == "delete":
                console.print("Original session archives are retained.")
        except (OSError, ValueError, ImportError) as exc:
            console.print(f"Memory: {exc}")
    elif command == "/where":
        console.print(f"{'工作目录' if zh else 'workspace'}: [cyan]{config.workspace}[/cyan]\n"
                      f"{'会话' if zh else 'session'}: [cyan]{state.session.id}[/cyan]\n"
                      f"{'权限模式' if zh else 'permission mode'}: [cyan]{_mode_label(state, zh)}[/cyan]\n"
                      f"sandbox: [cyan]{config.sandbox} / {config.sandbox_profile} / {config.sandbox_network}[/cyan]\n"
                      f"approved domains: {', '.join(state.agent.approved_domains) or '(none)'}\n"
                      "outside sandbox: hooks, MCP, browser, fetch, web_search")
    elif command == "/list":
        _print_sessions(console, zh)
    elif command == "/new":
        state.session = Session.new(config.model, str(config.workspace))
        state.rebuild()
        apply_active_harness(state, console)
        if evolution:
            evolution.reset()
        console.print(f"[green]{'已新建会话' if zh else 'Started session'} {state.session.id}[/green]")
    elif command == "/resume":
        loaded = _find_session(rest) if rest else None
        if loaded is None:
            console.print(f"[red]{'未找到会话' if zh else 'No session matching'} '{rest}'[/red]")
        else:
            state.session = loaded
            state.rebuild()
            apply_active_harness(state, console)
            if evolution:
                evolution.reset()
            console.print(f"[green]{'已恢复会话' if zh else 'Resumed session'} {state.session.id}[/green]")
    elif command == "/cost":
        _print_cost(state, console)
    elif command == "/share":
        console.print(state.session.transcript())
    elif command == "/fork":
        state.session = state.session.fork()
        state.rebuild()
        apply_active_harness(state, console)
        if evolution:
            evolution.reset()
        console.print(f"[green]{'已创建分支会话' if zh else 'Forked into session'} {state.session.id}[/green]")
    elif command == "/undo":
        console.print(checkpoints.undo(state.config.sandbox_policy))
    elif command == "/plan":
        state.plan_mode = _toggle(rest, state.plan_mode)
        state.agent.plan_mode = state.plan_mode
        console.print(f"{'计划模式' if zh else 'plan mode'}: {_status(state.plan_mode, zh)}")
    elif command == "/approve":
        _set_mode(state, "bypass" if _toggle(rest, state.permission.allow_all) else "standard")
        console.print(f"{'自动批准' if zh else 'auto-approve'}: {_status(state.permission.allow_all, zh)}")
    elif command == "/mode":
        if not rest and sys.stdin.isatty():
            rest = choice("选择权限模式：" if zh else "Choose permission mode:", options=[
                ("standard", "手动：操作前询问" if zh else "Manual: ask before protected actions"),
                ("acceptEdits", "自动编辑：文件改动免询问，命令仍询问" if zh else
                 "Accept edits: file changes run, shell still asks"),
                ("plan", "计划：只读探索" if zh else "Plan: read-only exploration"),
                ("dontAsk", "不询问：未预授权操作直接拒绝" if zh else "Don't ask: deny unapproved actions"),
                ("bypass", "极限放权：跳过工具审批" if zh else "Full access: skip tool approval prompts"),
            ], default=_active_mode(state) if _active_mode(state) != "aggressive" else "standard")
        if rest:
            try:
                _set_mode(state, rest)
            except ValueError:
                console.print(f"[red]{'未知权限模式' if zh else 'Unknown permission mode'}: {rest}[/red]")
                return False
        console.print(f"{'权限模式' if zh else 'permission mode'}: [cyan]{_mode_label(state, zh)}[/cyan]")
    elif command == "/reasoning":
        state.show_reasoning = _toggle(rest, state.show_reasoning)
        console.print(f"{'推理过程' if zh else 'reasoning'}: {_status(state.show_reasoning, zh)}")
    elif command == "/mcp":
        _print_mcp(state, console)
    elif command == "/attach":
        try:
            attachment = state.attach_file(rest)
            state.pending_attachments.append(attachment)
            console.print(f"[green]{'已附加到下一条消息' if zh else 'Attached to next message'}: "
                          f"{attachment['name']}[/green]")
        except (OSError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
    elif command == "/model":
        try:
            if not rest and sys.stdin.isatty() and state.config.profiles:
                rest = choice("选择模型配置：" if zh else "Choose a model profile:",
                              options=[(name, (f"{name} · {profile.get('provider', state.config.provider)}/"
                                              f"{profile.get('model', state.config.model)}"))
                                       for name, profile in state.config.profiles.items()])
            state.select_model(rest)
            apply_active_harness(state, console)
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
    elif command in ("/harness", "/track", "/feedback", "/evolve", "/evaluate", "/adopt", "/evolution"):
        workflow = evolution or EvolutionSession(config.workspace)
        try:
            if command == "/harness":
                if not sys.stdin.isatty():
                    raise ValueError("Harness² 选择菜单需要交互式终端")
                _harness_menu(state, console, workflow)
            elif command == "/track":
                workflow.track(console)
            elif command == "/feedback":
                if not workflow.task or not rest:
                    raise ValueError("请先跟踪并发送任务，再输入反馈内容")
                workflow.feedback = rest
                console.print(f"[green]已记录反馈：[/green] {rest}")
            elif command == "/evolve":
                verifier = rest or (Prompt.ask("请输入验证命令")
                                    if sys.stdin.isatty() else "")
                workflow.refine(verifier, console)
            elif command == "/evaluate":
                parts = [part.strip('"') for part in shlex.split(rest, posix=False)]
                if not parts or len(parts) > 2:
                    raise ValueError("用法：/evaluate <独立评测清单> [任务 ID]")
                workflow.evaluate(Path(parts[0]).expanduser().resolve(), parts[1] if len(parts) == 2 else "", console)
            elif command == "/adopt":
                workflow.adopt(state, console)
            else:
                workflow.status(console)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, KeyboardInterrupt) as exc:
            console.print(f"[red]Harness²：{exc or '已中断'}[/red]")
    else:
        console.print(f"[red]{'未知命令' if zh else 'Unknown command'}: {command}[/red]")
    return False


def _harness_menu(state: State, console: Console, workflow: EvolutionSession) -> None:
    options = [("back", "返回对话"), ("status", "查看当前状态"), ("track", "保存任务前快照")]
    if workflow.task:
        options += [("feedback", "记录用户反馈"), ("evolve", "生成并验证候选")]
    if workflow.run_dir:
        options.append(("candidates", "查看并选择候选"))
    if workflow.candidate:
        options.append(("evaluate", "选择独立评测题"))
    if workflow.evaluated:
        options.append(("adopt", "采纳已通过评测的候选"))
    action = choice("Harness² · 选择下一步（方向键选择，回车确认）", options=options, default="back")
    if action == "track":
        workflow.track(console)
    elif action == "status":
        workflow.status(console)
    elif action == "feedback":
        feedback = Prompt.ask("请输入实际观察到的反馈").strip()
        if feedback:
            workflow.feedback = feedback
            console.print("[green]反馈已记录。[/green]")
    elif action == "evolve":
        choices = [("back", "返回"), ("pytest", "运行项目 pytest 测试"), ("custom", "输入自定义验证命令")]
        if workflow.verifier:
            choices.insert(1, ("last", f"再次使用：{workflow.verifier}"))
        source = choice("选择验证方式", options=choices, default="back")
        if source != "back":
            verifier = (workflow.verifier if source == "last" else "python -m pytest -q" if source == "pytest"
                        else Prompt.ask("请输入可信的验证命令"))
            workflow.refine(verifier, console)
    elif action == "candidates":
        labels = workflow.candidates(console)
        if labels:
            selected = choice("选择通过验证的候选（改选后需重新评测）",
                              options=[("back", "保留当前候选"), *[(label, label) for label in labels]], default="back")
            if selected != "back" and (not workflow.candidate or selected != workflow.candidate.name):
                workflow.select_candidate(selected, console)
    elif action == "evaluate":
        manifests = list(dict.fromkeys(path.resolve() for root in (state.config.workspace, Path.cwd())
                                        for path in (root / "evals").glob("*.json")))
        selected = choice("选择独立评测清单", options=[("back", "返回"),
                          *[(str(path), str(path)) for path in manifests], ("custom", "输入清单路径")], default="back")
        if selected != "back":
            manifest = Path(Prompt.ask("清单路径") if selected == "custom" else selected).expanduser().resolve()
            cases = json.loads(manifest.read_text(encoding="utf-8"))
            case_id = choice("选择与训练任务不同的题目", options=[("back", "返回"), ("", "运行全部题目"),
                             *[(case["id"], f"{case['id']} · {case.get('category', '任务')}") for case in cases]],
                             default="back")
            if case_id != "back":
                workflow.evaluate(manifest, case_id, console)
    elif action == "adopt":
        workflow.adopt(state, console)


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
    from ..providers.openai_compatible import session_cost

    usage = Usage(**state.session.usage)
    known, complete = session_cost(state.session.usage_by_model)
    if state.config.routing.get("embedding") and any(
            item.get("stage") in ("mcp_search", "skill_search") for item in state.session.routing_events):
        complete = False
    zh = is_chinese(state.session.messages)
    money = f"${known:.4f}" if complete else (
        f"未知（已知部分 ${known:.4f}）" if zh else f"unknown (known portion ${known:.4f})")
    console.print(
        f"{'请求数' if zh else 'requests'}: [cyan]{usage.requests}[/cyan]   "
        f"{'输入' if zh else 'input'}: [cyan]{usage.input_tokens}[/cyan]   "
        f"{'输出' if zh else 'output'}: [cyan]{usage.output_tokens}[/cyan]   "
        f"{'预估费用' if zh else 'est. cost'}: [cyan]{money}[/cyan]"
    )
    for child in state.session.subagents.values():
        try:
            sub = Session.load(child["id"], state.session.store)
        except (OSError, ValueError, KeyError):
            continue
        console.print(f"{'子任务' if zh else 'child'} {child['id']}: "
                      f"{sub.model} · {sub.usage['input_tokens']} input / "
                      f"{sub.usage['output_tokens']} output tokens")


def _print_mcp(state: State, console: Console) -> None:
    state.run_async(state.mcp.connect())
    if not state.mcp_tools:
        console.print("[dim]未加载 MCP 工具（检查 ~/.coderill/mcp.json）[/dim]" if
                      is_chinese(state.session.messages) else
                      "[dim]no MCP tools loaded (see ~/.coderill/mcp.json)[/dim]")
        return
    for tool in state.mcp_tools:
        console.print(f"[cyan]{tool.name}[/cyan]  [dim]{_short(tool.description, 80)}[/dim]")
