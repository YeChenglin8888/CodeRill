"""Tests for the task plan model and tools."""

from pathlib import Path

import pytest

from coderill.taskplan import StaleRevision, TaskPlan, UnknownTask
from coderill.tools import Registry
from coderill.tools.taskplan import task_plan_tools


def test_cycle_is_rejected_without_partial_update():
    plan = TaskPlan("dag")
    plan.create(["first", "second"], expected_revision=0)
    plan.update("t2", expected_revision=1, depends_on=["t1"])
    original = plan.snapshot()
    with pytest.raises(ValueError, match="cycle"):
        plan.update("t1", expected_revision=2, status="in_progress", depends_on=["t2"])
    assert plan.snapshot() == original


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
