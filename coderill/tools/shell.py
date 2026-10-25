"""The bash tool.

Runs a command through the user's shell from the workspace root. On Windows the
default shell is cmd.exe, where ``pwd``, ``ls``, ``cat`` and friends do not
exist, so if Git Bash is installed we route through it and posixify drive-letter
paths. Running arbitrary shell commands is the point of a coding agent, so this
is dangerous by design: the permission layer asks before a bash call runs.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import ClassVar

from ..sandbox import DockerJob, SandboxPolicy
from .base import Tool

MAX_OUTPUT_CHARS = 20_000

# C:\Users\me\file -> /c/Users/me/file
_DRIVE = re.compile(r"(?<![\w/])([A-Za-z]):[\\/]")


class BashTool(Tool):
    name = "bash"
    description = "Run a shell command from the workspace root and return its combined output."
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run."},
            "timeout": {"type": "integer", "description": "Seconds before the command is killed. Default 120."},
            "network_domains": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["command"],
    }

    def __init__(self, root: Path, policy: SandboxPolicy | None = None) -> None:
        self.root = root
        self.policy = policy

    def execute(self, command: str, timeout: int = 120, network_domains: list[str] | None = None) -> str:
        try:
            proc = _run(command, self.root, timeout, self.policy, tuple(network_domains or ()))
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"

        parts = []
        if proc.stdout:
            parts.append(proc.stdout)
        if proc.stderr:
            parts.append(("[stderr]\n" + proc.stderr) if proc.stdout else proc.stderr)

        output = "".join(parts).strip() or "(no output)"
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + f"\n... [output truncated at {MAX_OUTPUT_CHARS} chars]"
        return f"exit code: {proc.returncode}\n{output}"


def _run(command: str, cwd: Path, timeout: int, policy: SandboxPolicy | None = None,
         network_domains: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    """Run ``command`` in the right shell for this platform."""
    if policy and policy.enabled:
        result = DockerJob(policy, extra_domains=network_domains).run(["bash", "-lc", command], timeout=timeout)
        return subprocess.CompletedProcess(command, result.returncode, result.stdout.decode(errors="replace"),
                                           result.stderr.decode(errors="replace"))
    shell = _git_bash()
    if shell:
        command = _DRIVE.sub(lambda m: f"/{m.group(1).lower()}/", command).replace("\\", "/")
        return subprocess.run(
            [shell, "-lc", command], cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    return subprocess.run(
        command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )


def _git_bash() -> str | None:
    """Path to Git Bash on Windows, or None on POSIX (where /bin/sh is fine)."""
    if os.name != "nt":
        return None
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if Path(candidate).is_file():
            return candidate
    git = shutil.which("git")
    if git:
        candidate = Path(git).parent.parent / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None
