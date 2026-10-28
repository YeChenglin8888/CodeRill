"""Structured tool boundaries and review behavior."""

import difflib
import json
import subprocess

import pytest

from coderill import checkpoints
from coderill.permissions import GrantStore, Mode, ReviewManager
from coderill.tools import build_registry
from coderill.tools.base import ToolError
from coderill.tools.network import FetchTool, WebSearchTool


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _patch(path, before, after):
    return "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                          fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="")) + "\n"


def test_git_views_and_multi_read(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("old\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-m", "initial")
    (tmp_path / "a.txt").write_text("new\n")
    registry = build_registry(tmp_path)
    assert "a.txt" in registry.dispatch("git_status", {})
    assert "+new" in registry.dispatch("git_diff", {"path": "a.txt"})
    assert "initial" in registry.dispatch("git_log", {"max_entries": 1})
    assert "=== a.txt ===" in registry.dispatch("read_multi", {"paths": ["a.txt"]})
    assert "outside the workspace" in registry.dispatch("read_multi", {"paths": ["../x"]})


def test_multi_file_patch_review_refresh_and_undo(tmp_path):
    _git(tmp_path, "init")
    checkpoints.clear()
    (tmp_path / "a.txt").write_text("before a\n")
    (tmp_path / "b.txt").write_text("before b\n")
    patch = _patch("a.txt", "before a\n", "after a\n") + _patch("b.txt", "before b\n", "after b\n")
    registry = build_registry(tmp_path)
    manager = ReviewManager(tmp_path, store=GrantStore(tmp_path / "grants.json"))
    call = {"id": "p", "name": "apply_patch", "arguments": {"patch": patch}}
    review = manager.request(registry.get("apply_patch"), call)
    assert len(review["targets"]) == 2
    assert "a.txt" in review["diff"] and "b.txt" in review["diff"]
    (tmp_path / "b.txt").write_text("human edit\n")
    assert manager.refresh(review)["id"] != review["id"]
    (tmp_path / "b.txt").write_text("before b\n")
    assert "Applied patch" in registry.dispatch("apply_patch", {"patch": patch})
    assert (tmp_path / "a.txt").read_text() == "after a\n"
    assert (tmp_path / "b.txt").read_text() == "after b\n"
    assert "restored" in checkpoints.undo()
    assert "restored" in checkpoints.undo()
    assert (tmp_path / "a.txt").read_text() == "before a\n"
    assert (tmp_path / "b.txt").read_text() == "before b\n"


def test_delete_requires_review_and_directory_opt_in(tmp_path):
    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "a.txt").write_text("important\n")
    registry = build_registry(tmp_path)
    manager = ReviewManager(tmp_path, store=GrantStore(tmp_path / "grants.json"))
    call = {"id": "d", "name": "delete_path", "arguments": {"path": "sub", "recursive": True}}
    review = manager.request(registry.get("delete_path"), call)
    assert "important" in review["diff"]
    assert registry.dispatch("delete_path", {"path": "sub"}).startswith("Error:")
    assert "Deleted directory" in registry.dispatch("delete_path", {"path": "sub", "recursive": True})
    assert not folder.exists()
    assert "restored" in checkpoints.undo()
    assert (folder / "a.txt").read_text() == "important\n"


def test_patch_and_delete_are_reviewed_even_in_aggressive_mode(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "a.txt").write_text("old\n")
    registry = build_registry(tmp_path)
    manager = ReviewManager(tmp_path, mode=Mode.AGGRESSIVE, store=GrantStore(tmp_path / "grants.json"))
    patch = _patch("a.txt", "old\n", "new\n")
    assert manager.request(registry.get("apply_patch"),
                           {"id": "p", "name": "apply_patch", "arguments": {"patch": patch}}) is not None
    assert manager.request(registry.get("delete_path"),
                           {"id": "d", "name": "delete_path", "arguments": {"path": "a.txt"}}) is not None
    assert registry.dispatch("diagnostics", {"command": "echo checked"}).startswith("diagnostic passed")


def test_accept_edits_allows_patch_but_still_reviews_shell(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "a.txt").write_text("old\n")
    registry = build_registry(tmp_path)
    manager = ReviewManager(tmp_path, mode=Mode.ACCEPT_EDITS, store=GrantStore(tmp_path / "grants.json"))
    patch = _patch("a.txt", "old\n", "new\n")
    assert manager.request(registry.get("apply_patch"),
                           {"name": "apply_patch", "arguments": {"patch": patch}}) is None
    assert manager.request(registry.get("bash"),
                           {"name": "bash", "arguments": {"command": "pytest -q"}}) is not None


def test_patch_rejects_workspace_escape(tmp_path):
    _git(tmp_path, "init")
    patch = _patch("../outside.txt", "before\n", "after\n")
    assert build_registry(tmp_path).dispatch("apply_patch", {"patch": patch}).startswith("Error:")


def test_public_fetch_and_search_parse_bounded_results(monkeypatch):
    from coderill.tools import network

    class Response:
        status = 200

        def __init__(self, data):
            self.data = data

        def read(self, *_):
            return self.data

        def getheader(self, name, default):
            return "text/plain"

    class Connection:
        def __init__(self, host, port, timeout):
            self.host = host

        def request(self, method, path, body=None, headers=None):
            self.method = method

        def getresponse(self):
            if self.host == "search.parallel.ai":
                return Response(json.dumps({"result": {"content": [{"text": "https://example.com result"}]}}).encode())
            return Response(b"public content")

        def close(self):
            pass

    monkeypatch.setattr(network.socket, "getaddrinfo", lambda *_args: [(None, None, None, None, ("93.184.215.14", 80))])
    monkeypatch.setattr(network.http.client, "HTTPSConnection", Connection)
    assert FetchTool().execute("https://example.com") == "public content"
    assert "https://example.com" in WebSearchTool().execute("example")
    with pytest.raises(ToolError, match="not allowed"):
        FetchTool().execute("http://localhost/")
    with pytest.raises(ToolError, match="not allowed"):
        FetchTool().execute("http://127.0.0.1/")
