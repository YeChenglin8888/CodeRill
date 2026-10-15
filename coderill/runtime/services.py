"""Long-running commands with session-visible logs and explicit stop controls."""

from __future__ import annotations

import os
import signal
import subprocess
import uuid
from pathlib import Path

from ..session import Session
from ..tools.shell import _DRIVE, _git_bash


class ServiceProcessManager:
    def __init__(self, session: Session, workspace: Path) -> None:
        self.session = session
        self.workspace = workspace
        self.running: dict[str, subprocess.Popen] = {}

    def start(self, command: str) -> dict:
        process_id = uuid.uuid4().hex[:12]
        root = self.session.store.root / f"{self.session.id}.processes"
        root.mkdir(parents=True, exist_ok=True)
        stdout = root / f"{process_id}.stdout.log"
        stderr = root / f"{process_id}.stderr.log"
        options = {"cwd": self.workspace, "stdout": None, "stderr": None}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        shell = _git_bash()
        if shell:
            command = _DRIVE.sub(lambda match: f"/{match.group(1).lower()}/", command).replace("\\", "/")
            argv = [shell, "-lc", command]
        else:
            argv = command
            options["shell"] = True
        with stdout.open("ab") as out, stderr.open("ab") as err:
            options["stdout"], options["stderr"] = out, err
            process = subprocess.Popen(argv, **options)
        record = {"id": process_id, "pid": process.pid, "command": command,
                  "stdout": str(stdout), "stderr": str(stderr), "status": "running"}
        try:
            self.session.record_process(record)
        except Exception:
            _stop(process)
            raise
        self.running[process_id] = process
        return record

    def status(self, process_id: str) -> dict:
        record = dict(self.session.processes[process_id])
        process = self.running.get(process_id)
        if process is not None:
            if process.poll() is None:
                record["status"] = "running"
            elif record["status"] in ("running", "detached"):
                record["status"] = "exited"
                record["exit_code"] = process.returncode
                self.session.finish_process(process_id, "exited")
        return record

    def logs(self, process_id: str, max_chars: int = 20_000) -> str:
        record = self.status(process_id)
        output = []
        for stream in ("stdout", "stderr"):
            path = Path(record[stream])
            root = self.session.store.root / f"{self.session.id}.processes"
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("process log path is outside the session")
            text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            output.append(f"[{stream}]\n{text[-max_chars // 2:]}")
        return "\n".join(output)

    def stop(self, process_id: str) -> dict:
        process = self.running.get(process_id)
        if process is None:
            raise ValueError("process is detached; only the original runner can stop it safely")
        if process.poll() is not None:
            return self.status(process_id)
        _stop(process)
        self.session.finish_process(process_id, "stopped")
        return self.status(process_id)


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)
