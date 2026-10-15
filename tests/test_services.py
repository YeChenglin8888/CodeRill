"""Long-lived local process lifecycle without a model API."""

import sys
import time

from coderill.runtime.services import ServiceProcessManager
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
