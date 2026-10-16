"""Tool package: wires the individual tools into a registry."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .advanced import ApplyPatchTool, DeletePathTool, DiagnosticsTool, ReadMultiTool
from .base import Registry, Tool, ToolError
from .files import EditFileTool, ReadFileTool, WriteFileTool
from .git import GitDiffTool, GitLogTool, GitStatusTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .network import FetchTool, WebSearchTool
from .shell import BashTool
from .todo import TodoWriteTool

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Config
    from ..llm import LLM

__all__ = ["Registry", "Tool", "ToolError", "build_registry"]


def build_registry(
    workspace: Path,
    *,
    llm: LLM | None = None,
    config: Config | None = None,
    extra_tools: list[Tool] | None = None,
) -> Registry:
    """Create a registry with the built-in tools plus any extras.

    The optional browser tool needs an enabled OpenAI-compatible model client.
    """
    root = Path(workspace)
    registry = Registry(root)
    registry.register(ReadFileTool(root))
    registry.register(WriteFileTool(root))
    registry.register(EditFileTool(root))
    registry.register(BashTool(root))
    registry.register(GlobTool(root))
    registry.register(GrepTool(root))
    for tool in (ReadMultiTool(root), GitStatusTool(root), GitDiffTool(root), GitLogTool(root),
                 DiagnosticsTool(root), ApplyPatchTool(root), DeletePathTool(root), FetchTool(), WebSearchTool()):
        registry.register(tool)
    registry.register(TodoWriteTool())

    if llm is not None and config is not None and config.browser_enabled and config.provider == "openai":
        from .browser import BrowserTaskTool

        registry.register(BrowserTaskTool(llm, config))

    for tool in extra_tools or ():
        registry.register(tool)
    return registry
