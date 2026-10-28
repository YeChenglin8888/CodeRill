"""Tool plumbing: the Tool base class, path resolution, and the registry.

A tool is a small class with a name, a JSON schema for its arguments, and an
``execute`` method. The registry turns the set of tools into the ``tools=[...]``
payload the API expects and dispatches calls back to the right one.

``read_only`` matters to the permission layer: read-only tools run freely, while
mutating ones ask the user first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from ..sandbox import SandboxPolicy, check_active_path, policy_scope

# Read-only tools may run without asking; mutating ones need consent.
MAX_OUTPUT_CHARS = 30_000


class ToolError(Exception):
    """An expected, user-facing tool failure (bad path, no match, ...)."""


@dataclass(frozen=True)
class ToolOutput:
    content: str
    image: bytes


class Tool:
    """Base class for a capability exposed to the model."""

    name: str = ""
    description: str = ""
    parameters: ClassVar[dict[str, Any]] = {}
    read_only: bool = False
    # Changes only in-process state (a task list, the plan) and never the
    # workspace or network, so it needs no consent either.
    local_only: bool = False
    # Long structured results (the browser tool returns JSON evidence) must not
    # be chopped in half, so they opt out of the generic truncation.
    truncate_output: bool = True

    @property
    def needs_consent(self) -> bool:
        """Whether running this tool must be approved by the user first."""
        return not (self.read_only or self.local_only)

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def execute(self, **kwargs: Any) -> str | ToolOutput:  # pragma: no cover - abstract
        raise NotImplementedError


def resolve_path(root: Path, path: str) -> Path:
    """Resolve ``path`` against ``root`` and refuse to escape it.

    Relative paths are interpreted from the workspace root; absolute paths are
    allowed only if they still land inside it.
    """
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    check_active_path(candidate)
    resolved = candidate.resolve()
    root = root.resolve()
    if resolved != root and root not in resolved.parents:
        raise ToolError(f"path '{path}' is outside the workspace root")
    check_active_path(resolved)
    return resolved


def truncate(text: str) -> str:
    """Keep tool output from blowing up the model's context window."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    head = MAX_OUTPUT_CHARS // 2
    tail = MAX_OUTPUT_CHARS // 2
    omitted = len(text) - head - tail
    return f"{text[:head]}\n... [{omitted} characters omitted] ...\n{text[-tail:]}"


class Registry:
    """Holds the tools and resolves paths relative to the workspace root."""

    def __init__(self, workspace: Path, policy: SandboxPolicy | None = None) -> None:
        self.workspace = workspace.resolve()
        self.policy = policy
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str | ToolOutput:
        """Run a tool by name, converting expected failures into text."""
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'."
        try:
            with policy_scope(self.policy):
                if self.policy and self.policy.enabled and self.policy.profile == "read-only" and name in (
                    "write_file", "edit_file", "delete_path", "apply_patch"):
                    raise ToolError("sandbox workspace is read-only")
                result = tool.execute(**arguments)
        except ToolError as exc:
            return f"Error: {exc}"
        except TypeError as exc:
            return f"Error: bad arguments for '{name}': {exc}"
        except Exception as exc:  # noqa: BLE001 - surface anything else to the model
            return f"Error: {name} failed: {exc}"
        return truncate(result) if isinstance(result, str) and tool.truncate_output else result
