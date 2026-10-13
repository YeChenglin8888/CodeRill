"""Tests for edit_file semantics: uniqueness, replace_all, and failures."""

from coderill.tools import build_registry


def _seed(registry, text):
    registry.dispatch("write_file", {"path": "f.txt", "content": text})


def test_edit_replaces_a_unique_match(tmp_path):
    registry = build_registry(tmp_path)
    _seed(registry, "alpha beta gamma")
    registry.dispatch("edit_file", {"file_path": "f.txt", "old_string": "beta", "new_string": "BETA"})
    assert (tmp_path / "f.txt").read_text() == "alpha BETA gamma"


def test_edit_rejects_an_ambiguous_match(tmp_path):
    registry = build_registry(tmp_path)
    _seed(registry, "x x x")
    out = registry.dispatch("edit_file", {"file_path": "f.txt", "old_string": "x", "new_string": "y"})
    assert out.startswith("Error:")
    assert "appears 3 times" in out
    assert (tmp_path / "f.txt").read_text() == "x x x"  # unchanged


def test_edit_replace_all(tmp_path):
    registry = build_registry(tmp_path)
    _seed(registry, "x x x")
    registry.dispatch(
        "edit_file", {"file_path": "f.txt", "old_string": "x", "new_string": "y", "replace_all": True}
    )
    assert (tmp_path / "f.txt").read_text() == "y y y"


def test_edit_missing_match_is_an_error(tmp_path):
    registry = build_registry(tmp_path)
    _seed(registry, "abc")
    out = registry.dispatch("edit_file", {"file_path": "f.txt", "old_string": "zzz", "new_string": "q"})
    assert out.startswith("Error:")
    assert "not found" in out


def test_edit_identical_strings_is_rejected(tmp_path):
    registry = build_registry(tmp_path)
    _seed(registry, "abc")
    out = registry.dispatch("edit_file", {"file_path": "f.txt", "old_string": "a", "new_string": "a"})
    assert out.startswith("Error:")


def test_edit_returns_a_diff(tmp_path):
    registry = build_registry(tmp_path)
    _seed(registry, "one\ntwo\nthree\n")
    out = registry.dispatch("edit_file", {"file_path": "f.txt", "old_string": "two", "new_string": "TWO"})
    assert "-two" in out and "+TWO" in out
