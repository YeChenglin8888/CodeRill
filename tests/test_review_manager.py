import pytest

from coderill.permissions import GrantStore, ReviewManager
from coderill.tools import build_registry
from coderill.tools.base import ToolError


def test_review_rejects_changed_file_and_rebuilds_diff(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("original\n")
    manager = ReviewManager(tmp_path, store=GrantStore(tmp_path / "grants.json"))
    tool = build_registry(tmp_path).get("write_file")
    call = {"id": "c1", "name": "write_file", "arguments": {"path": "a.txt", "content": "new\n"}}
    first = manager.request(tool, call)
    assert "-original" in first["diff"]
    target.write_text("human edit\n")
    refreshed = manager.refresh(first)
    assert refreshed["id"] != first["id"]
    assert "-human edit" in refreshed["diff"]
    assert manager.refresh(refreshed) is None


def test_saved_grant_is_exact_and_workspace_bound(tmp_path):
    store = GrantStore(tmp_path / "grants.json")
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    tool = build_registry(first_root).get("write_file")
    call = {"id": "c1", "name": "write_file", "arguments": {"path": "a.txt", "content": "new"}}
    first = ReviewManager(first_root, store=store)
    first.remember(first.request(tool, call))
    assert first.request(tool, call) is None
    assert ReviewManager(second_root, store=store).request(tool, call) is not None


def test_path_outside_workspace_cannot_be_reviewed(tmp_path):
    manager = ReviewManager(tmp_path, store=GrantStore(tmp_path / "grants.json"))
    tool = build_registry(tmp_path).get("write_file")
    call = {"id": "c1", "name": "write_file", "arguments": {"path": "../escape.txt", "content": "x"}}
    with pytest.raises(ToolError, match="outside the workspace"):
        manager.request(tool, call)
