"""Async shell execution with bounded output, timeout and cancellation."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..sandbox import DockerJob, SandboxError, SandboxPolicy
from ..tools.shell import _DRIVE, _git_bash

OUTPUT_LIMIT = 20_000


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    output: str
    status: str


class ProcessManager:
    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self.policy = policy

    async def run(self, command: str, cwd: Path, timeout: int, cancel: asyncio.Event,
                  network_domains: tuple[str, ...] = ()) -> ProcessResult:
        if self.policy and self.policy.enabled:
            job = DockerJob(self.policy, extra_domains=network_domains)
            operation = asyncio.create_task(asyncio.to_thread(job.run, ["bash", "-lc", command], timeout=timeout + 30))
            cancelled = asyncio.create_task(cancel.wait())
            try:
                done, _ = await asyncio.wait({operation, cancelled}, timeout=max(timeout, 1),
                                             return_when=asyncio.FIRST_COMPLETED)
                if operation in done:
                    try:
                        result = operation.result()
                    except SandboxError as exc:
                        return ProcessResult(None, f"Error: {exc}", "failed")
                    body = (result.stdout + (b"\n[stderr]\n" + result.stderr if result.stderr else b""))
                    return ProcessResult(result.returncode, body[:OUTPUT_LIMIT].decode(errors="replace") or "(no output)",
                                         "success" if result.returncode == 0 else "failed")
                clean = job.close()
                await asyncio.gather(operation, return_exceptions=True)
                message = "sandbox command cancelled" if cancelled in done else "sandbox command timed out"
                if not clean:
                    message += f"; cleanup pending for job {job.id}"
                return ProcessResult(None, message, "interrupted" if cancelled in done else "timeout")
            except asyncio.CancelledError:
                job.close()
                await asyncio.gather(operation, return_exceptions=True)
                raise
            finally:
                cancelled.cancel()
        shell = _git_bash()
        options = {"cwd": cwd, "stdout": asyncio.subprocess.PIPE, "stderr": asyncio.subprocess.PIPE}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        if shell:
            command = _DRIVE.sub(lambda match: f"/{match.group(1).lower()}/", command).replace("\\", "/")
            process = await asyncio.create_subprocess_exec(shell, "-lc", command, **options)
        else:
            process = await asyncio.create_subprocess_shell(command, **options)

        stdout = asyncio.create_task(_collect(process.stdout))
        stderr = asyncio.create_task(_collect(process.stderr))
        finished = asyncio.create_task(process.wait())
        cancelled = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait({finished, cancelled}, timeout=max(timeout, 1), return_when=asyncio.FIRST_COMPLETED)
            if finished in done:
                status = "success" if process.returncode == 0 else "failed"
            else:
                status = "interrupted" if cancelled in done else "timeout"
                await _stop(process)
            out, err = await asyncio.gather(stdout, stderr)
            body = out + ("\n[stderr]\n" + err if err else "")
            if not body:
                body = "(no output)"
            if len(body) > OUTPUT_LIMIT:
                body = body[:OUTPUT_LIMIT] + "\n... [output truncated]"
            return ProcessResult(process.returncode, body, status)
        finally:
            cancelled.cancel()
            if process.returncode is None:
                await _stop(process)
            await asyncio.gather(stdout, stderr, finished, return_exceptions=True)


async def _collect(stream: asyncio.StreamReader | None) -> str:
    if stream is None:
        return ""
    chunks: list[bytes] = []
    total = 0
    while chunk := await stream.read(4096):
        if total < OUTPUT_LIMIT:
            keep = chunk[: OUTPUT_LIMIT - total]
            chunks.append(keep)
            total += len(keep)
    return b"".join(chunks).decode(errors="replace")


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(process.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await process.wait()
