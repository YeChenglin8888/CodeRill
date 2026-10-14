"""Tests for the agent loop, using a scripted fake model (no network)."""

import coderill.session as session_mod
from coderill.agent import Agent
from coderill.config import Config
from coderill.permissions import Permission
from coderill.session import Session
from coderill.tools import build_registry


class FakeLLM:
    """Replays a fixed list of assistant messages, one per turn."""

    def __init__(self, script):
        self.script = list(script)

    def stream(self, messages, tools=None, deadline=None):
        message = self.script.pop(0)
        for char in message.get("content") or "":
            yield ("delta", char)
        yield ("final", message, None)


TOOL_CALL = {
    "role": "assistant",
    "content": None,
    "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "write_file", "arguments": '{"path": "a.txt", "content": "hi"}'}}
    ],
}


def _agent(tmp_path, monkeypatch, script, *, allow_all=True):
    monkeypatch.setattr(session_mod, "SESSION_DIR", tmp_path)
    config = Config(api_key="test", workspace=tmp_path)
    session = Session.new("test", str(tmp_path))
    registry = build_registry(tmp_path)
    return Agent(config, session, registry, llm=FakeLLM(script), permissions=Permission(allow_all=allow_all)), session


def test_loop_executes_a_tool_then_finishes(tmp_path, monkeypatch):
    agent, session = _agent(tmp_path, monkeypatch, [TOOL_CALL, {"role": "assistant", "content": "done"}])
    result = agent.run("make a file")
    assert result == "done"
    assert (tmp_path / "a.txt").read_text() == "hi"
    assert [m["role"] for m in session.messages] == ["user", "assistant", "tool", "assistant"]


def test_loop_stops_when_no_tool_is_called(tmp_path, monkeypatch):
    agent, session = _agent(tmp_path, monkeypatch, [{"role": "assistant", "content": "just talking"}])
    assert agent.run("hello") == "just talking"
    assert [m["role"] for m in session.messages] == ["user", "assistant"]


def test_loop_reports_turn_limit(tmp_path, monkeypatch):
    agent, _ = _agent(tmp_path, monkeypatch, [TOOL_CALL] * 3)
    agent.config.max_turns = 2
    assert "turn limit" in agent.run("go")


def test_mutating_tool_refused_without_consent(tmp_path, monkeypatch):
    agent, session = _agent(tmp_path, monkeypatch, [TOOL_CALL, {"role": "assistant", "content": "ok"}], allow_all=False)
    agent.run("make a file")
    assert not (tmp_path / "a.txt").exists()
    tool_msg = next(m for m in session.messages if m["role"] == "tool")
    assert tool_msg["content"].startswith("refused")


def test_plan_mode_refuses_mutating_tools(tmp_path, monkeypatch):
    agent, session = _agent(tmp_path, monkeypatch, [TOOL_CALL, {"role": "assistant", "content": "ok"}])
    agent.plan_mode = True
    agent.run("make a file")
    assert not (tmp_path / "a.txt").exists()
    tool_msg = next(m for m in session.messages if m["role"] == "tool")
    assert "plan mode" in tool_msg["content"]


def test_task_plan_survives_session_resume(tmp_path, monkeypatch):
    create_plan = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "plan_1",
                "type": "function",
                "function": {"name": "task_create", "arguments": '{"contents": ["fix bug"], "expected_revision": 0}'},
            }
        ],
    }
    agent, session = _agent(tmp_path, monkeypatch, [create_plan])
    agent.config.max_turns = 1
    agent.run("make a plan")

    resumed_session = Session.load(session.id)
    resumed = Agent(
        agent.config,
        resumed_session,
        build_registry(tmp_path),
        llm=FakeLLM([]),
        permissions=Permission(allow_all=True),
    )
    assert resumed.plan.snapshot() == agent.plan.snapshot()
