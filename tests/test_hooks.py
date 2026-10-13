"""Tests for the tool hooks."""

from coderill.hooks import Hooks, load_hooks


def test_pre_hook_vetoes_with_exit_code_2():
    hooks = Hooks([{"matcher": "bash", "command": "exit 2"}], [])
    assert hooks.run_pre("bash", {}) is not None


def test_pre_hook_allows_on_exit_zero():
    hooks = Hooks([{"matcher": "bash", "command": "exit 0"}], [])
    assert hooks.run_pre("bash", {}) is None


def test_matcher_star_matches_everything():
    hooks = Hooks([{"matcher": "*", "command": "exit 2"}], [])
    assert hooks.run_pre("write_file", {}) is not None


def test_matcher_does_not_match_other_tools():
    hooks = Hooks([{"matcher": "bash", "command": "exit 2"}], [])
    assert hooks.run_pre("write_file", {}) is None


def test_load_missing_file_is_empty(tmp_path):
    assert not load_hooks(tmp_path / "none.json")
