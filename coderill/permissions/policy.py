"""User consent for tool calls, with scoped persistent grants.

Read-only and local-only tools run the moment the model asks. Anything that
touches the workspace, the network or a subprocess goes through a small policy:

    classify the call -> a Request(action, target)
        -> a matching saved grant?          ALLOW
        -> the current mode's defaults      (aggressive may ALLOW)
        -> otherwise                        ASK the user

Modes:

  * ``standard``   -- ask for everything not already granted
  * ``aggressive`` -- auto-allow the safe action set (file writes inside the
                      workspace, shell) without asking
  * ``bypass``     -- allow everything; calls are still classified and reported

"Always allow" stores a *scoped* grant — an exact path, a command prefix, a host,
or one server/tool pair — never an open-ended "yes to everything". When there is
nobody to ask (one-shot mode, a library embedding), a mutating call is refused
rather than blocking on input that can never arrive.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..tools.base import Tool

Asker = Callable[[str, dict[str, Any]], Any]

# Actions that "aggressive" mode approves automatically.
_AGGRESSIVE_ACTIONS = {"filesystem", "shell"}


class Mode(str, Enum):
    STANDARD = "standard"
    AGGRESSIVE = "aggressive"
    BYPASS = "bypass"


@dataclass(frozen=True)
class Request:
    """A classified permission request."""

    tool: str
    action: str
    target: str
    workspace: str = ""


@dataclass(frozen=True)
class Grant:
    """A saved, scoped allow rule."""

    action: str
    pattern: str
    tool: str | None = None
    workspace: str = ""

    def matches(self, request: Request) -> bool:
        if request.action != self.action:
            return False
        if self.tool is not None and self.tool != request.tool:
            return False
        return self.workspace == request.workspace and self.pattern == request.target


class GrantStore:
    """Persistent grants under ``~/.coderill/permissions.json``."""

    def __init__(self, path: str | Path | None = None) -> None:
        from . import GRANTS_FILE

        self.path = Path(path) if path is not None else GRANTS_FILE
        self.grants: list[Grant] = self._load()

    def _load(self) -> list[Grant]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        return [Grant(**item) for item in data.get("grants", []) if isinstance(item, dict)]

    def add(self, grant: Grant) -> None:
        if grant not in self.grants:
            self.grants.append(grant)
            self._save()

    def match(self, request: Request) -> Grant | None:
        for grant in self.grants:
            if grant.matches(request):
                return grant
        return None

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"grants": [asdict(g) for g in self.grants]}, indent=2))


def classify(tool_name: str, arguments: dict[str, Any], workspace: str = "") -> Request | None:
    """Map a tool call to a permission request, or None if it is not classifiable."""
    if tool_name in ("write_file", "edit_file"):
        target = arguments.get("path") or arguments.get("file_path") or ""
        return Request(tool_name, "filesystem", str(target), workspace)
    if tool_name in ("bash", "process_start"):
        return Request(tool_name, "shell", str(arguments.get("command", "")), workspace)
    if tool_name == "process_stop":
        return Request(tool_name, "process", str(arguments.get("process_id", "")), workspace)
    if tool_name == "browser_task":
        return Request(tool_name, "network", _host(str(arguments.get("url", ""))), workspace)
    if tool_name == "agent":
        return Request(tool_name, "agent", "", workspace)
    if tool_name.startswith("mcp__"):
        return Request(tool_name, "mcp_tool", tool_name, workspace)
    return None


def default_scope(request: Request) -> Grant | None:
    """The narrowest useful grant for a request, used by "always allow"."""
    if request.action == "filesystem":
        return Grant("filesystem", request.target, request.tool, request.workspace)
    if request.action == "shell":
        return Grant("shell", request.target, request.tool, request.workspace) if request.target.strip() else None
    if request.action in ("network", "mcp_tool", "process"):
        return Grant(request.action, request.target, request.tool, request.workspace)
    return None  # agent: not persistently grantable


class Permission:
    """Decides whether a tool call may run."""

    def __init__(
        self,
        ask: Asker | None = None,
        allow_all: bool = False,
        *,
        mode: Mode | None = None,
        store: GrantStore | None = None,
        workspace: str = "",
    ) -> None:
        self.ask = ask
        self.mode = Mode.BYPASS if allow_all else (mode or Mode.STANDARD)
        self.store = store if store is not None else GrantStore()
        self.workspace = workspace
        self._always: set[str] = set()

    @property
    def allow_all(self) -> bool:
        return self.mode is Mode.BYPASS

    @allow_all.setter
    def allow_all(self, value: bool) -> None:
        self.mode = Mode.BYPASS if value else Mode.STANDARD

    def check(self, tool: Tool, arguments: dict[str, Any]) -> str | None:
        """Return None to allow the call, or a refusal string for the model."""
        if not tool.needs_consent:
            return None

        request = classify(tool.name, arguments, self.workspace) or Request(tool.name, "tool", "", self.workspace)

        if self.mode is Mode.BYPASS:
            return None
        if tool.name in self._always or self.store.match(request) is not None:
            return None
        if self.mode is Mode.AGGRESSIVE and request.action in _AGGRESSIVE_ACTIONS:
            return None

        if self.ask is None:
            return f"refused: {tool.name} needs approval and no one is available to grant it"

        decision = self.ask(tool.name, arguments)
        if decision == "always":
            scope = default_scope(request)
            if scope is not None:
                # A scoped grant, not an open-ended "yes to this tool forever".
                self.store.add(scope)
            else:
                self._always.add(tool.name)
            return None
        if decision is True:
            return None
        return f"refused: the user declined to run {tool.name}"

    def always_allow(self, tool_name: str) -> None:
        """Stop asking about this tool for the rest of the session."""
        self._always.add(tool_name)


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or ""
