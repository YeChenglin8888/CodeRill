"""Tests for the task plan model and tools."""

from pathlib import Path

import pytest

import coderill.session as session_mod
from coderill.agent import PLAN_RECONCILE, Agent
from coderill.config import Config
from coderill.permissions import Permission
from coderill.session import Session
from coderill.taskplan import StaleRevision, TaskPlan, UnknownTask
from coderill.tools import Registry, build_registry
from coderill.tools.taskplan import task_plan_tools


def test_cycle_is_rejected_without_partial_update():
    plan = TaskPlan("dag")
    plan.create(["first", "second"], expected_revision=0)
    plan.update("t2", expected_revision=1, depends_on=["t1"])
    original = plan.snapshot()
    with pytest.raises(ValueError, match="cycle"):
        plan.update("t1", expected_revision=2, status="in_progress", depends_on=["t2"])
    assert plan.snapshot() == original


class _FakeLLM:
    def __init__(self, script):
        self.script = list(script)

    def stream(self, messages, tools=None, deadline=None):
        message = self.script.pop(0)
        for char in message.get("content") or "":
            yield ("delta", char)
        yield ("final", message, None)


def _registry(plan):
    registry = Registry(Path("."))
    for tool in task_plan_tools(plan):
        registry.register(tool)
    return registry


def test_create_appends_tasks_and_bumps_revision():
    plan = TaskPlan()
    out = plan.create(["write parser", "add tests"], expected_revision=0)
    assert "t1" in out and "t2" in out
    assert plan.revision == 1
    assert [task.status for task in plan.tasks] == ["pending", "pending"]


def test_stale_revision_is_rejected():
    plan = TaskPlan()
    plan.create(["a"], 0)
    with pytest.raises(StaleRevision):
        plan.create(["b"], 0)


def test_invalid_create_does_not_partially_change_plan():
    plan = TaskPlan()
    with pytest.raises(ValueError):
        plan.create(["valid", ""], 0, mode="dag")
    assert plan.snapshot() == {"mode": "linear", "revision": 0, "tasks": []}


def test_dag_ready_set_follows_dependencies():
    plan = TaskPlan("dag")
    plan.create(["a", "b"], 0)
    plan.update("t2", plan.revision, depends_on=["t1"])
    assert plan.ready() == ["t1"]
    plan.update("t1", plan.revision, status="done")
    assert plan.ready() == ["t2"]


def test_unknown_task_is_rejected():
    plan = TaskPlan()
    plan.create(["a"], 0)
    with pytest.raises(UnknownTask):
        plan.update("t9", plan.revision, status="done")


def test_invalid_dependency_does_not_partially_update_task():
    plan = TaskPlan()
    plan.create(["a"], 0)
    before = plan.snapshot()
    with pytest.raises(UnknownTask):
        plan.update("t1", plan.revision, status="done", depends_on=["t9"])
    assert plan.snapshot() == before


def test_revise_changes_wording():
    plan = TaskPlan()
    plan.create(["old wording"], 0)
    plan.revise("t1", "new wording", plan.revision)
    assert plan.tasks[0].content == "new wording"


def test_unfinished_lists_non_done_tasks():
    plan = TaskPlan()
    plan.create(["a", "b"], 0)
    plan.update("t1", plan.revision, status="done")
    assert [task.id for task in plan.unfinished()] == ["t2"]


def test_snapshot_shape():
    plan = TaskPlan()
    plan.create(["a"], 0)
    snapshot = plan.snapshot()
    assert snapshot["revision"] == 1
    assert snapshot["tasks"][0]["id"] == "t1"


def test_stale_revision_surfaces_as_a_tool_error():
    plan = TaskPlan()
    registry = _registry(plan)
    registry.dispatch("task_create", {"contents": ["a"], "expected_revision": 0})
    out = registry.dispatch("task_create", {"contents": ["b"], "expected_revision": 0})
    assert out.startswith("Error:")
    assert "stale revision" in out


def test_agent_nudges_once_when_the_plan_is_unfinished(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSION_DIR", tmp_path)
    script = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "task_create", "arguments": '{"contents": ["a"], "expected_revision": 0}'},
                }
            ],
        },
        {"role": "assistant", "content": "done"},
        {"role": "assistant", "content": "final after nudge"},
    ]
    config = Config(api_key="x", workspace=tmp_path)
    session = Session.new("m", str(tmp_path))
    agent = Agent(config, session, build_registry(tmp_path), llm=_FakeLLM(script), permissions=Permission(allow_all=True))

    result = agent.run("go")

    assert result == "final after nudge"
    assert len(agent.plan.tasks) == 1
    assert any(m.get("content") == PLAN_RECONCILE for m in session.messages)
