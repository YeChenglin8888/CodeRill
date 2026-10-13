"""Tests for glob, grep and todo_write."""

from coderill.tools import build_registry


def test_glob_finds_files_recursively(tmp_path):
    registry = build_registry(tmp_path)
    registry.dispatch("write_file", {"path": "a.py", "content": "x"})
    registry.dispatch("write_file", {"path": "sub/b.py", "content": "y"})
    out = registry.dispatch("glob", {"pattern": "**/*.py"})
    assert "a.py" in out and "b.py" in out


def test_glob_reports_no_matches(tmp_path):
    registry = build_registry(tmp_path)
    assert "no files match" in registry.dispatch("glob", {"pattern": "**/*.zzz"})


def test_grep_finds_matches_with_line_numbers(tmp_path):
    registry = build_registry(tmp_path)
    registry.dispatch("write_file", {"path": "a.py", "content": "first\nneedle here\n"})
    out = registry.dispatch("grep", {"pattern": "needle", "include": "*.py"})
    assert "a.py:2" in out and "needle here" in out


def test_grep_invalid_regex_is_an_error(tmp_path):
    registry = build_registry(tmp_path)
    assert registry.dispatch("grep", {"pattern": "("}).startswith("Error:")


def test_todo_write_tracks_progress(tmp_path):
    registry = build_registry(tmp_path)
    out = registry.dispatch(
        "todo_write",
        {"tasks": [{"content": "step one", "status": "done"}, {"content": "step two", "status": "pending"}]},
    )
    assert "1/2" in out

    tool = registry.get("todo_write")
    assert "step two" in tool.render()


def test_todo_write_rejects_bad_status(tmp_path):
    registry = build_registry(tmp_path)
    out = registry.dispatch("todo_write", {"tasks": [{"content": "x", "status": "nope"}]})
    assert out.startswith("Error:")
