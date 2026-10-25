"""Long-lived local process lifecycle without a model API."""

import sys
import time

from coderill.runtime.services import ServiceProcessManager
from coderill.sandbox import SandboxError
from coderill.session import Session, SessionStore


def test_process_start_logs_stop_and_replay_marks_detached(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), store)
    manager = ServiceProcessManager(session, tmp_path)
    command = f'"{sys.executable}" -u -c "import time; print(12345); time.sleep(30)"'
    record = manager.start(command)
    try:
        assert record["pid"] > 0
        assert Session.load(session.id, store).processes[record["id"]]["status"] == "detached"
        for _ in range(30):
            if "12345" in manager.logs(record["id"]):
                break
            time.sleep(0.1)
        assert "12345" in manager.logs(record["id"])
    finally:
        stopped = manager.stop(record["id"])
    assert stopped["status"] == "stopped"
    assert Session.load(session.id, store).processes[record["id"]]["status"] == "stopped"


def test_sandbox_stop_cleans_up_when_logs_are_unavailable(monkeypatch, tmp_path):
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    manager = ServiceProcessManager(session, tmp_path)
    session.record_process({"id": "job", "backend": "docker", "status": "running"})

    class Job:
        closed = False

        def close(self):
            self.closed = True
            return True

    job = Job()
    manager.sandbox_jobs["job"] = job

    def unavailable(_record):
        raise SandboxError("engine offline")

    monkeypatch.setattr(manager, "_sandbox_logs", unavailable)
    assert manager.stop("job")["status"] == "stopped"
    assert job.closed
    assert "engine offline" in session.processes["job"]["last_logs"]
