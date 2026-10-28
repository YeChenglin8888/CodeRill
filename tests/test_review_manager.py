import pytest

from coderill.permissions import GrantStore, Mode, ReviewManager, classify, default_scope
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
    assert ReviewManager(first_root, store=store).request(tool, call) is not None
    assert ReviewManager(second_root, store=store).request(tool, call) is not None


def test_path_outside_workspace_cannot_be_reviewed(tmp_path):
    manager = ReviewManager(tmp_path, store=GrantStore(tmp_path / "grants.json"))
    tool = build_registry(tmp_path).get("write_file")
    call = {"id": "c1", "name": "write_file", "arguments": {"path": "../escape.txt", "content": "x"}}
    with pytest.raises(ToolError, match="outside the workspace"):
        manager.request(tool, call)


def test_accept_edits_auto_approves_file_changes_only(tmp_path):
    registry = build_registry(tmp_path)
    manager = ReviewManager(tmp_path, mode=Mode.ACCEPT_EDITS, store=GrantStore(tmp_path / "grants.json"))
    for name, arguments in (("write_file", {"path": "a.txt", "content": "x"}),
                            ("delete_path", {"path": "a.txt"})):
        assert manager.request(registry.get(name), {"name": name, "arguments": arguments}) is None
    assert manager.request(registry.get("bash"),
                           {"name": "bash", "arguments": {"command": "echo hello"}}) is not None
    with pytest.raises(ToolError, match="outside the workspace"):
        manager.request(registry.get("write_file"),
                        {"name": "write_file", "arguments": {"path": "../escape.txt", "content": "x"}})


def test_dont_ask_uses_saved_grants_and_refuses_other_calls(tmp_path):
    registry = build_registry(tmp_path)
    store = GrantStore(tmp_path / "grants.json")
    standard = ReviewManager(tmp_path, store=store)
    call = {"name": "bash", "arguments": {"command": "git status"}}
    standard.remember(standard.request(registry.get("bash"), call))
    manager = ReviewManager(tmp_path, mode=Mode.DONT_ASK, store=store)
    assert manager.request(registry.get("bash"), call) is None
    with pytest.raises(PermissionError, match="dontAsk"):
        manager.request(registry.get("bash"), {"name": "bash", "arguments": {"command": "echo hello"}})


def test_old_persistent_file_grants_do_not_bypass_new_session_review(tmp_path):
    store = GrantStore(tmp_path / "grants.json")
    store.add(default_scope(classify("write_file", {"path": str(tmp_path / "a.txt")}, str(tmp_path))))
    manager = ReviewManager(tmp_path, store=store)
    call = {"name": "write_file", "arguments": {"path": "a.txt", "content": "x"}}
    assert manager.request(build_registry(tmp_path).get("write_file"), call) is not None
