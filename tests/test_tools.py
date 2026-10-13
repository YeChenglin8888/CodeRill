"""Tests for the file and shell tools, and the path guard."""

from coderill.tools import build_registry


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


def test_bash_runs_and_reports_exit_code(tmp_path):
    registry = build_registry(tmp_path)
    out = registry.dispatch("bash", {"command": "echo hi"})
    assert "hi" in out
    assert "exit code: 0" in out


def test_bash_reports_nonzero_exit(tmp_path):
    registry = build_registry(tmp_path)
    out = registry.dispatch("bash", {"command": "exit 3"})
    assert "exit code: 3" in out


def test_unknown_tool(tmp_path):
    registry = build_registry(tmp_path)
    assert "unknown tool" in registry.dispatch("does_not_exist", {})
