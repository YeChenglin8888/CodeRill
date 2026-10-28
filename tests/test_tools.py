"""Tests for the file and shell tools, and the path guard."""

import os

import pytest

from coderill.config import Config
from coderill.tools import build_registry, shell


def test_browser_tool_is_registered_only_when_enabled_for_compatible_provider(tmp_path):
    client = object()
    disabled = Config(api_key="unused", workspace=tmp_path)
    assert "browser_task" not in build_registry(tmp_path, llm=client, config=disabled).names()
    disabled.browser_enabled = True
    assert "browser_task" in build_registry(tmp_path, llm=client, config=disabled).names()
    disabled.provider = "anthropic"
    assert "browser_task" not in build_registry(tmp_path, llm=client, config=disabled).names()


def test_write_then_read_roundtrip(tmp_path):
    registry = build_registry(tmp_path)
    out = registry.dispatch("write_file", {"path": "a.txt", "content": "hello\nworld\n"})
    assert "Created" in out

    text = registry.dispatch("read_file", {"path": "a.txt"})
    assert "hello" in text and "world" in text
    assert "1\t" in text  # line numbers are present


def test_write_creates_parent_directories(tmp_path):
    registry = build_registry(tmp_path)
    registry.dispatch("write_file", {"path": "sub/dir/b.txt", "content": "x"})
    assert (tmp_path / "sub" / "dir" / "b.txt").read_text() == "x"


def test_read_missing_file_is_an_error(tmp_path):
    registry = build_registry(tmp_path)
    out = registry.dispatch("read_file", {"path": "nope.txt"})
    assert out.startswith("Error:")


def test_path_traversal_is_blocked(tmp_path):
    registry = build_registry(tmp_path)
    out = registry.dispatch("read_file", {"path": "../../etc/passwd"})
    assert out.startswith("Error:")
    assert "outside the workspace" in out


def test_file_tools_accept_noncanonical_workspace_root(tmp_path):
    from coderill.tools.base import ToolError
    from coderill.tools.files import ReadFileTool
    from coderill.tools.glob_tool import GlobTool
    from coderill.tools.grep import GrepTool

    (tmp_path / "nested").mkdir()
    (tmp_path / "record.txt").write_text("needle")
    root = tmp_path / "nested" / ".."
    assert "needle" in ReadFileTool(root).execute("record.txt")
    assert "record.txt" in GrepTool(root).execute("needle")
    assert "record.txt" in GlobTool(root).execute("*.txt")
    with pytest.raises(ToolError, match="outside the workspace"):
        ReadFileTool(root).execute("../outside.txt")


def test_bash_runs_and_reports_exit_code(tmp_path):
    registry = build_registry(tmp_path)
    out = registry.dispatch("bash", {"command": "echo hi"})
    assert "hi" in out
    assert "exit code: 0" in out


def test_bash_reports_nonzero_exit(tmp_path):
    registry = build_registry(tmp_path)
    out = registry.dispatch("bash", {"command": "exit 3"})
    assert "exit code: 3" in out


@pytest.mark.skipif(os.name != "nt", reason="Windows Git Bash selection")
def test_git_bash_does_not_select_wsl(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(shell.shutil, "which", lambda name: r"C:\PortableGit\cmd\git.exe" if name == "git" else r"C:\Windows\System32\bash.exe")
        patch.setattr(shell.Path, "is_file", lambda path: str(path) == r"C:\PortableGit\bin\bash.exe")
        selected = shell._git_bash()
    assert selected == r"C:\PortableGit\bin\bash.exe"


def test_unknown_tool(tmp_path):
    registry = build_registry(tmp_path)
    assert "unknown tool" in registry.dispatch("does_not_exist", {})
