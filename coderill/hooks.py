"""User shell hooks around tool calls.

Definitions live in ``~/.coderill/hooks.json``:

    {"PreToolUse":  [{"matcher": "bash", "command": "..."}],
     "PostToolUse": [{"matcher": "*",    "command": "..."}]}

Each hook is a shell command fed the tool call as JSON on stdin. A pre hook
vetoes the call with exit code 2, and its stderr becomes the reason the model
sees. Post hooks only observe. A hook that errors or hangs is skipped: hooks
assist the loop, they never get to kill it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HOOKS_FILE = Path.home() / ".coderill" / "hooks.json"
TIMEOUT = 10


class Hooks:
    def __init__(self, pre: list[dict], post: list[dict]) -> None:
        self.pre = pre
        self.post = post

    def __bool__(self) -> bool:
        return bool(self.pre or self.post)

    def run_pre(self, tool_name: str, tool_input: dict) -> str | None:
        """Return a veto reason, or None to let the call proceed."""
        for hook in self.pre:
            if not _matches(hook, tool_name):
                continue
            code, out, err = _fire(hook, tool_name, tool_input)
            if code == 2:
                return err.strip() or out.strip() or "blocked by a pre hook"
        return None

    def run_post(self, tool_name: str, tool_input: dict, result: str) -> None:
        for hook in self.post:
            if _matches(hook, tool_name):
                _fire(hook, tool_name, tool_input, result)


class CombinedHooks(Hooks):
    """Keep user hooks and adopted declarative Harness² rules in one tool loop."""

    def __init__(self, original: Hooks, harness: Hooks) -> None:
        self.original, self.harness = original, harness

    def __bool__(self) -> bool:
        return bool(self.original or self.harness)

    def run_pre(self, tool_name: str, tool_input: dict) -> str | None:
        return self.original.run_pre(tool_name, tool_input) or self.harness.run_pre(tool_name, tool_input)

    def run_post(self, tool_name: str, tool_input: dict, result: str) -> str | None:
        self.original.run_post(tool_name, tool_input, result)
        return self.harness.run_post(tool_name, tool_input, result)


def load_hooks(path: Path = HOOKS_FILE) -> Hooks:
    path = Path(path)
    if not path.exists():
        return Hooks([], [])
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return Hooks([], [])
    return Hooks(data.get("PreToolUse", []) or [], data.get("PostToolUse", []) or [])


def _matches(hook: dict, tool_name: str) -> bool:
    matcher = hook.get("matcher", "*")
    if matcher in ("*", tool_name):
        return True
    return matcher.endswith("*") and tool_name.startswith(matcher[:-1])


def _fire(hook: dict, tool_name: str, tool_input: dict, result: str | None = None):
    payload = {"tool_name": tool_name, "tool_input": tool_input}
    if result is not None:
        payload["tool_result"] = result
    try:
        proc = subprocess.run(
            hook.get("command", ""),
            shell=True,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception:  # noqa: BLE001 - a broken hook is skipped, never fatal
        return -1, "", ""
