"""Long-running commands with session-visible logs and explicit stop controls."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import uuid
from pathlib import Path

from ..sandbox import DockerJob, SandboxError, SandboxPolicy, _pid_alive, docker
from ..session import Session
from ..tools.shell import _DRIVE, _git_bash


class ServiceProcessManager:
    def __init__(self, session: Session, workspace: Path, policy: SandboxPolicy | None = None) -> None:
        self.session = session
        self.workspace = workspace
        self.policy = policy
        self.running: dict[str, subprocess.Popen] = {}
        self.sandbox_jobs: dict[str, DockerJob] = {}
        for process_id, record in list(session.processes.items()):
            if record.get("backend") == "docker" and record.get("status") in ("running", "detached", "cleanup-pending"):
                owner = Path.home() / ".coderill" / "sandbox-jobs" / f"{record.get('job_id', '')}.json"
                if owner.exists():
                    try:
                        pid = json.loads(owner.read_text(encoding="utf-8"))["pid"]
                        if isinstance(pid, int) and not _pid_alive(pid):
                            session.finish_process(process_id, "cleanup-pending")
                    except (OSError, ValueError, KeyError, TypeError):
                        session.finish_process(process_id, "cleanup-pending")
                    continue
                clean = DockerJob.recover(record, session.id)
                session.finish_process(process_id, "stopped" if clean else "cleanup-pending")

    def start(self, command: str, network_domains: tuple[str, ...] = (), ports: tuple[int, ...] = ()) -> dict:
        process_id = uuid.uuid4().hex[:12]
        if self.policy and self.policy.enabled:
            job = DockerJob(self.policy, extra_domains=network_domains, ports=ports, session_id=self.session.id)
            job.start(["bash", "-lc", command], background=True)
            record = {"id": process_id, "command": command, "status": "running", "backend": "docker",
                      "container": job.worker, "gateway": job.gateway, "volume": job.volume,
                      "network": job.network_name, "job_id": job.id, "ports": list(ports)}
            try:
                self.session.record_process(record)
            except (OSError, ValueError, RuntimeError, KeyError):
                job.close()
                raise
            self.sandbox_jobs[process_id] = job
            return record
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
        if record.get("backend") == "docker":
            if record["status"] not in ("running", "detached"):
                return record
            state = self._sandbox_state(record)
            record["status"] = state[0]
            record["exit_code"] = state[1]
            if state[0] in ("exited", "gateway-failed") and process_id in self.sandbox_jobs:
                try:
                    record["last_logs"] = self._sandbox_logs(record)
                except SandboxError as exc:
                    record["last_logs"] = f"Sandbox logs unavailable: {exc}"
                clean = self.sandbox_jobs.pop(process_id).close()
                record["status"] = state[0] if clean else "cleanup-pending"
                self.session.processes[process_id]["last_logs"] = record["last_logs"]
                self.session.finish_process(process_id, record["status"])
            return record
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
        if record.get("backend") == "docker":
            if process_id in self.sandbox_jobs:
                return self._sandbox_logs(record)[-max_chars:]
            return record.get("last_logs", "")[-max_chars:]
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
        record = self.session.processes[process_id]
        if record.get("backend") == "docker":
            job = self.sandbox_jobs.get(process_id)
            if job is None:
                if record.get("status") not in ("running", "detached"):
                    return dict(record)
                raise ValueError("sandbox process is detached; ownership could not be verified")
            try:
                output = self._sandbox_logs(record)
            except SandboxError as exc:
                output = f"Sandbox logs unavailable: {exc}"
            clean = job.close()
            self.sandbox_jobs.pop(process_id, None)
            self.session.processes[process_id]["last_logs"] = output
            self.session.finish_process(process_id, "stopped" if clean else "cleanup-pending")
            return dict(self.session.processes[process_id])
        process = self.running.get(process_id)
        if process is None:
            raise ValueError("process is detached; only the original runner can stop it safely")
        if process.poll() is not None:
            return self.status(process_id)
        _stop(process)
        self.session.finish_process(process_id, "stopped")
        return self.status(process_id)

    def _sandbox_state(self, record: dict) -> tuple[str, int | None]:
        job = self.sandbox_jobs.get(record["id"])
        if job is None:
            return ("detached", None)
        result = docker("inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", record["container"])
        running, code = result.stdout.decode().strip().split()
        if running != "true":
            return ("exited", int(code))
        gateway = docker("inspect", "-f", "{{.State.Running}}", record["gateway"]).stdout.strip()
        return ("running", None) if gateway == b"true" else ("gateway-failed", None)

    @staticmethod
    def _sandbox_logs(record: dict) -> str:
        result = docker("logs", "--tail", "1000", record["container"])
        gateway = docker("logs", "--tail", "100", record["gateway"])
        return (result.stdout + result.stderr + gateway.stdout + gateway.stderr).decode(errors="replace")[-20_000:]

    def close(self) -> None:
        for process_id in list(self.sandbox_jobs):
            try:
                self.stop(process_id)
            except (OSError, ValueError, RuntimeError, KeyError):
                self.session.finish_process(process_id, "cleanup-pending")


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
