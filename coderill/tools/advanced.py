"""Structured multi-file reads, diagnostics, patches, and deletion."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import ClassVar

from .. import checkpoints
from ..sandbox import DockerJob, SandboxPolicy, check_active_mutation
from .base import Tool, ToolError, resolve_path
from .files import ReadFileTool
from .shell import _run


class ReadMultiTool(Tool):
    name = "read_multi"
    description = "Read several workspace text files in one call, with a bounded combined result."
    read_only = True
    parameters: ClassVar[dict] = {"type": "object", "properties": {
        "paths": {"type": "array", "items": {"type": "string"}},
    }, "required": ["paths"]}

    def __init__(self, root: Path) -> None:
        self.reader = ReadFileTool(root)

    def execute(self, paths: list[str]) -> str:
        if not paths or len(paths) > 20 or any(not isinstance(path, str) for path in paths):
            raise ToolError("paths must contain 1 to 20 file names")
        parts = []
        for path in paths:
            parts.append(f"=== {path} ===\n{self.reader.execute(path)}")
        return "\n\n".join(parts)[:30_000]


class DiagnosticsTool(Tool):
    name = "diagnostics"
    description = "Run a test, lint, or type-check command and report its exit code."
    parameters: ClassVar[dict] = {"type": "object", "properties": {
        "command": {"type": "string"}, "timeout": {"type": "integer"},
        "network_domains": {"type": "array", "items": {"type": "string"}},
    }, "required": ["command"]}

    def __init__(self, root: Path, policy: SandboxPolicy | None = None) -> None:
        self.root = root
        self.policy = policy

    def execute(self, command: str, timeout: int = 300, network_domains: list[str] | None = None) -> str:
        if not 1 <= timeout <= 3600:
            raise ToolError("timeout must be between 1 and 3600 seconds")
        try:
            result = _run(command, self.root, timeout, self.policy, tuple(network_domains or ()))
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"diagnostic timed out after {timeout} seconds") from exc
        output = (result.stdout + result.stderr)[:20_000]
        if result.returncode:
            return f"Error: diagnostic exited {result.returncode}\n{output}"
        return f"diagnostic passed (exit 0)\n{output}"


class DeletePathTool(Tool):
    name = "delete_path"
    description = "Delete a workspace file or, with recursive=true, a directory."
    parameters: ClassVar[dict] = {"type": "object", "properties": {
        "path": {"type": "string"}, "recursive": {"type": "boolean"},
    }, "required": ["path"]}

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def execute(self, path: str, recursive: bool = False) -> str:
        lexical = self.root / path
        if lexical.is_symlink():
            raise ToolError("delete the symlink separately")
        target = resolve_path(self.root, path)
        check_active_mutation(target)
        if target == self.root:
            raise ToolError("cannot delete the workspace root")
        if not target.exists():
            raise ToolError(f"path not found: {path}")
        if target.is_file():
            checkpoints.record(target)
            target.unlink()
            checkpoints.seal(target)
            return f"Deleted {path}"
        if not target.is_dir() or not recursive:
            raise ToolError("directory deletion requires recursive=true")
        files = list(target.rglob("*"))
        file_count = sum(item.is_file() for item in files)
        if any(item.is_symlink() for item in files):
            raise ToolError("directory contains a symlink; remove it separately")
        for item in sorted((item for item in files if item.is_file()), reverse=True):
            checkpoints.record(item)
            item.unlink()
            checkpoints.seal(item)
        for item in sorted((item for item in files if item.is_dir()), key=lambda p: len(p.parts), reverse=True):
            item.rmdir()
        target.rmdir()
        return f"Deleted directory {path} ({file_count} files)"


def patch_targets(root: Path, patch: str, policy: SandboxPolicy | None = None) -> list[Path]:
    if not patch.strip() or len(patch) > 1_000_000:
        raise ToolError("patch must be non-empty and under 1 MB")
    if any(marker in patch for marker in ("rename from ", "rename to ", "copy from ", "copy to ")):
        raise ToolError("rename and copy patches are not supported")
    if any(line.startswith(("new file mode ", "deleted file mode ", "old mode ", "new mode ", "index "))
           and "120000" in line
           for line in patch.splitlines()):
        raise ToolError("symlink patches are not supported")
    result = _apply_git(root, patch, "--numstat", "-z", policy=policy)
    targets = []
    for row in result.stdout.split(b"\0"):
        if not row:
            continue
        fields = row.split(b"\t", 2)
        if len(fields) != 3:
            raise ToolError("patch contains an unsupported path format")
        path = fields[2].decode("utf-8", errors="strict")
        targets.append(resolve_path(root.resolve(), path))
    for line in patch.splitlines():
        if not line.startswith(("--- ", "+++ ")):
            continue
        name = line[4:].split("\t", 1)[0]
        if name == "/dev/null":
            continue
        if not name.startswith(("a/", "b/")):
            raise ToolError("patch paths must use a/ or b/ prefixes")
        targets.append(resolve_path(root.resolve(), name[2:]))
    if not targets:
        raise ToolError("patch changes no files")
    for target in targets:
        check_active_mutation(target)
    return list(dict.fromkeys(targets))


def _apply_git(root: Path, patch: str, *args: str,
               policy: SandboxPolicy | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        if policy and policy.enabled:
            result = DockerJob(policy).run(["git", "-c", "core.hooksPath=/dev/null", "apply", *args, "-"],
                                            input=patch.encode(), timeout=20)
        else:
            result = subprocess.run(["git", "apply", *args, "-"], input=patch.encode(), cwd=root,
                                    capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"patch failed: {exc}") from exc
    if result.returncode:
        raise ToolError(result.stderr.decode(errors="replace").strip() or "patch cannot be applied")
    return result


class ApplyPatchTool(Tool):
    name = "apply_patch"
    description = "Apply a unified diff to workspace files after checking every target."
    parameters: ClassVar[dict] = {"type": "object", "properties": {
        "patch": {"type": "string"}, "dry_run": {"type": "boolean"},
    }, "required": ["patch"]}

    def __init__(self, root: Path, policy: SandboxPolicy | None = None) -> None:
        self.root = root.resolve()
        self.policy = policy

    def execute(self, patch: str, dry_run: bool = False) -> str:
        targets = patch_targets(self.root, patch, self.policy)
        _apply_git(self.root, patch, "--check", policy=self.policy)
        if dry_run:
            return "Patch can be applied to " + ", ".join(str(path.relative_to(self.root)) for path in targets)
        checkpoints.record_many(targets)
        try:
            _apply_git(self.root, patch, policy=self.policy)
        finally:
            checkpoints.seal_many(targets)
        return "Applied patch to " + ", ".join(str(path.relative_to(self.root)) for path in targets)
