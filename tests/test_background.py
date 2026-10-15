import asyncio

from coderill.agent.subagents import SubagentService
from coderill.config import Config
from coderill.runtime import BackgroundTaskManager
from coderill.session import Session, SessionStore
from coderill.taskplan import TaskPlan


def test_background_completion_notifies_once(tmp_path):
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path))

    async def scenario():
        manager = BackgroundTaskManager(session)
        task_id = manager.start("read", "child", lambda: asyncio.sleep(0, result="done"))
        await asyncio.sleep(0.01)
        assert manager.status(task_id)["status"] == "complete"
        assert len(manager.notifications(inject=True)) == 1
        assert manager.notifications() == []
        restored = Session.load(session.id, session.store)
        assert restored.background_tasks[task_id]["notified"]
        assert restored.messages[-1]["synthetic"] is True
        assert "done" in restored.messages[-1]["content"]
        assert restored.turns == 0

    asyncio.run(scenario())


def test_restart_marks_unfinished_background_job_interrupted(tmp_path):
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path))
    session.start_background({
        "id": "job", "description": "work", "child_session_id": "child",
        "status": "running", "result": "", "notified": False,
    })
    loaded = Session.load(session.id, session.store)
    assert loaded.background_tasks["job"]["status"] == "interrupted"
    manager = BackgroundTaskManager(loaded)
    assert [item["id"] for item in manager.notifications()] == ["job"]
    assert loaded.messages == []


def test_background_result_reconciles_plan_once_before_notification(tmp_path):
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path))
    plan = TaskPlan()
    plan.create(["inspect"], 0)
    plan.update("t1", plan.revision, status="in_progress")
    service = SubagentService(Config(api_key="unused", workspace=tmp_path), session, plan=plan)
    session.start_background({
        "id": "job", "description": "inspect", "child_session_id": "child",
        "status": "complete", "result": "verified", "notified": False,
        "plan_task_id": "t1", "plan_revision": 1,
    })
    assert [item["id"] for item in service.collect_background()] == ["job"]
    assert plan.tasks[0].status == "done"
    assert session.plan_snapshot["tasks"][0]["status"] == "done"
    assert service.collect_background() == []
    restored = Session.load(session.id, session.store)
    assert restored.plan_snapshot["tasks"][0]["status"] == "done"
    assert len([item for item in restored.messages if item.get("synthetic")]) == 1


def test_stale_pending_task_is_not_marked_done_by_late_background_result(tmp_path):
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path))
    plan = TaskPlan()
    plan.create(["original"], 0)
    plan.revise("t1", "changed", plan.revision)
    service = SubagentService(Config(api_key="unused", workspace=tmp_path), session, plan=plan)
    session.start_background({
        "id": "job", "description": "original", "child_session_id": "child",
        "status": "complete", "result": "done", "notified": False,
        "plan_task_id": "t1", "plan_revision": 1,
    })
    service.collect_background()
    assert plan.tasks[0].status == "pending"
    assert session.background_tasks["job"]["notified"]
