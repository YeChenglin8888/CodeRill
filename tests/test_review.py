"""Tests for the trusted pre-write review."""

from coderill.review import build_prewrite_review


def test_write_review_shows_a_diff(tmp_path):
    (tmp_path / "a.txt").write_text("old line\n")
    diff = build_prewrite_review("write_file", {"path": "a.txt", "content": "new line\n"}, tmp_path)
    assert "-old line" in diff
    assert "+new line" in diff


def test_write_review_of_a_new_file_is_all_additions(tmp_path):
    diff = build_prewrite_review("write_file", {"path": "new.txt", "content": "hello\n"}, tmp_path)
    assert "+hello" in diff


def test_edit_review_shows_the_proposed_change(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    diff = build_prewrite_review("edit_file", {"file_path": "a.py", "old_string": "1", "new_string": "2"}, tmp_path)
    assert "-x = 1" in diff
    assert "+x = 2" in diff


def test_edit_review_returns_empty_when_the_edit_would_not_apply(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    diff = build_prewrite_review("edit_file", {"file_path": "a.py", "old_string": "absent", "new_string": "y"}, tmp_path)
    assert diff == ""


def test_shell_effects_are_not_precomputed(tmp_path):
    assert build_prewrite_review("bash", {"command": "rm -rf build"}, tmp_path) == ""


def test_review_never_escapes_the_workspace(tmp_path):
    assert build_prewrite_review("write_file", {"path": "../../etc/passwd", "content": "x"}, tmp_path) == ""
