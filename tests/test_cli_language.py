import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import set_app
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from coderill.agent.runtime import TurnResult
from coderill.app import cli
from coderill.language import is_chinese
from coderill.permissions import Mode
from coderill.providers.types import ChatStreamEvent


def test_latest_human_message_sets_language():
    messages = [{"role": "user", "content": "请修复这个问题"},
                {"role": "user", "content": "Continue the previous response", "synthetic": True}]
    assert is_chinese(messages)
    messages.append({"role": "user", "content": "Explain the result in English"})
    assert not is_chinese(messages)


def test_approval_options_explain_scope_and_default_to_deny(monkeypatch):
    captured = {}

    def fake_choice(message, **kwargs):
        captured.update(message=message, **kwargs)
        return "approve"

    monkeypatch.setattr(cli, "choice", fake_choice)
    assert cli._approval_choice(True) == "approve"
    assert [value for value, _ in captured["options"]] == ["approve", "always", "deny"]
    assert "同类操作" in captured["options"][1][1]
    assert captured["default"] == "deny"


def test_cli_review_uses_selected_decision(monkeypatch):
    decisions = []

    class Agent:
        def run_turn(self, text, attachments):
            return TurnResult("waiting", review={"id": "r1", "call": {"name": "write_file",
                                                   "arguments": {"path": "a.txt"}}, "diff": "+hello"})

        def resume_turn(self, review_id, decision):
            decisions.append((review_id, decision))
            return TurnResult("complete")

    state = SimpleNamespace(session=SimpleNamespace(messages=[{"role": "user", "content": "请写文件"}],
                                                    pending_review=None, incomplete_calls={}, last_status="complete"),
                            agent=Agent(), pending_attachments=[], run_async=lambda result: result)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "choice", lambda *args, **kwargs: "deny")
    assert cli._run_once(state, "请写文件", Console(file=io.StringIO())) == 0
    assert decisions == [("r1", "deny")]


def test_cli_feedback_stops_before_stream_output(monkeypatch):
    events = []
    class Status:
        def __enter__(self):
            events.append("start")
            return self

        def __exit__(self, *_):
            events.append("exit")

        def stop(self):
            events.append("stop")

    console = Console(file=io.StringIO(), force_terminal=True)
    monkeypatch.setattr(console, "status", lambda *args, **kwargs: Status())
    agent = SimpleNamespace(on_event=lambda event: events.append(event.text))
    original = agent.on_event
    state = SimpleNamespace(agent=agent, session=SimpleNamespace(messages=[]),
                            run_async=lambda value: (agent.on_event(ChatStreamEvent("text_delta", text="hello")), value)[1])
    assert cli._run_with_feedback(state, "done", console) == "done"
    assert events == ["start", "stop", "hello", "exit"]
    assert agent.on_event is original


def test_help_follows_session_language():
    output = io.StringIO()
    console = Console(file=output, force_terminal=False)
    state = SimpleNamespace(session=SimpleNamespace(messages=[{"role": "user", "content": "你好"}]),
                            config=SimpleNamespace())
    cli._handle_command(state, console, "/help")
    assert "查看帮助" in output.getvalue()
    state.session.messages.append({"role": "user", "content": "Help me"})
    output.seek(0)
    output.truncate()
    cli._handle_command(state, console, "/help")
    assert "show this help" in output.getvalue()


def test_mode_and_model_commands_offer_configured_choices(monkeypatch):
    menus = []

    def choose(message, **kwargs):
        menus.append(kwargs["options"])
        return "standard" if len(menus) == 1 else "fast"

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "choice", choose)
    config = SimpleNamespace(profiles={"fast": {"provider": "openai", "model": "small"}},
                             provider="openai", model="default")
    state = SimpleNamespace(session=SimpleNamespace(messages=[{"role": "user", "content": "请选择"}]),
                            config=config, permission=SimpleNamespace(mode=Mode.BYPASS),
                            select_model=lambda profile: setattr(config, "model", profile))
    console = Console(file=io.StringIO(), force_terminal=False)
    cli._handle_command(state, console, "/mode")
    cli._handle_command(state, console, "/model")
    assert state.permission.mode is Mode.STANDARD
    assert config.model == "fast"
    assert [value for value, _ in menus[0]] == ["standard", "aggressive", "bypass"]
    assert menus[1][0][0] == "fast"


def test_repl_uses_framed_multiline_editor_with_explicit_send(monkeypatch, tmp_path):
    captured = {}

    class FakePromptSession:
        def __init__(self, **kwargs):
            self.app = SimpleNamespace(layout=SimpleNamespace(current_window=SimpleNamespace(dont_extend_height=None)))

        def prompt(self, message, **kwargs):
            captured.update(message=message, **kwargs)
            return "/exit"

    monkeypatch.setattr(cli, "PromptSession", FakePromptSession)
    monkeypatch.setattr(cli, "HISTORY_FILE", tmp_path / "history")
    state = SimpleNamespace(session=SimpleNamespace(messages=[{"role": "user", "content": "你好"}]),
                            config=SimpleNamespace())
    assert cli._repl(state, Console(file=io.StringIO())) == 0
    assert captured["multiline"] and captured["show_frame"]
    assert captured["message"] == ""
    assert captured["style"].style_rules
    assert "发送" in captured["bottom_toolbar"]
    calls = []
    buffer = SimpleNamespace(validate_and_handle=lambda: calls.append("send"),
                             insert_text=lambda value: calls.append(value))
    event = SimpleNamespace(current_buffer=buffer)
    bindings = captured["key_bindings"].bindings
    bindings[0].handler(event)
    bindings[1].handler(event)
    assert calls == ["send", "\n"]


def test_welcome_panel_shows_project_specific_help_at_wide_and_narrow_widths():
    state = SimpleNamespace(session=SimpleNamespace(messages=[], turns=0, id="session-1"),
                            config=SimpleNamespace(model="demo-model", workspace=Path("project"),
                                                   sandbox="off", sandbox_profile="workspace-write",
                                                   sandbox_network="restricted"))
    for width in (60, 100):
        output = io.StringIO()
        cli._print_welcome(state, Console(file=output, width=width, force_terminal=False))
        rendered = output.getvalue()
        assert "CodeRill" in rendered
        assert "demo-model" in rendered
        assert "/attach" in rendered and "/mode" in rendered
        assert cli.BRAND_MARK in rendered


def test_framed_editor_grows_with_input_lines():
    async def scenario():
        session = PromptSession(output=DummyOutput(), multiline=True, show_frame=True,
                                bottom_toolbar="Enter send")
        session.app.layout.current_window.dont_extend_height = lambda: True
        with set_app(session.app):
            one_line = session.app.layout.container.preferred_height(80, 24).preferred
            session.default_buffer.insert_text("first\nsecond")
            two_lines = session.app.layout.container.preferred_height(80, 24).preferred
            session.default_buffer.reset()
            session.default_buffer.insert_text("a" * 180)
            wrapped = session.app.layout.container.preferred_height(80, 24).preferred
        assert two_lines == one_line + 1
        assert wrapped > one_line

    asyncio.run(scenario())
