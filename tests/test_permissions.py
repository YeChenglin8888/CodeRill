"""Tests for the permission layer: classification, modes and scoped grants."""

import coderill.permissions as permissions_mod
from coderill.permissions import GrantStore, Mode, Permission, classify, default_scope
from coderill.tools import build_registry
from coderill.tools.agent import AgentTool


def test_read_only_allowed_without_an_asker(tmp_path):
    registry = build_registry(tmp_path)
    assert Permission().check(registry.get("read_file"), {}) is None


def test_mutating_refused_without_an_asker(tmp_path):
    registry = build_registry(tmp_path)
    assert Permission().check(registry.get("write_file"), {}) is not None


def test_asker_can_allow(tmp_path):
    registry = build_registry(tmp_path)
    assert Permission(ask=lambda name, args: True).check(registry.get("write_file"), {}) is None


def test_asker_can_deny(tmp_path):
    registry = build_registry(tmp_path)
    assert Permission(ask=lambda name, args: False).check(registry.get("write_file"), {}).startswith("refused")


def test_always_allow_skips_the_asker(tmp_path):
    registry = build_registry(tmp_path)
    calls = []
    permission = Permission(ask=lambda name, args: calls.append(name) or False)
    permission.always_allow("write_file")
    assert permission.check(registry.get("write_file"), {}) is None
    assert calls == []


def test_bypass_mode_allows_everything(tmp_path):
    registry = build_registry(tmp_path)
    assert Permission(allow_all=True).check(registry.get("bash"), {"command": "rm -rf /"}) is None


def test_classify_maps_tools_to_requests():
    assert classify("write_file", {"path": "a.py"}).action == "filesystem"
    assert classify("bash", {"command": "pytest -q"}).action == "shell"
    assert classify("browser_task", {"url": "https://example.com/x"}).target == "example.com"
    assert classify("mcp__fs__read", {}).action == "mcp_tool"
    assert classify("read_file", {}) is None


def test_default_scope_narrows_shell_to_the_exact_command():
    grant = default_scope(classify("bash", {"command": "git status --short"}))
    assert grant.pattern == "git status --short"


def test_always_creates_a_path_scoped_grant(tmp_path):
    store = GrantStore(tmp_path / "grants.json")
    answers = iter(["always", False])
    permission = Permission(ask=lambda name, args: next(answers), store=store)
    registry = build_registry(tmp_path)

    assert permission.check(registry.get("write_file"), {"path": "src/a.py", "content": "x"}) is None
    # A different path is not covered by the grant, so the asker runs again.
    refusal = permission.check(registry.get("write_file"), {"path": "other.py", "content": "x"})
    assert refusal.startswith("refused")
    assert store.grants[0].pattern == "src/a.py"


def test_aggressive_mode_auto_allows_filesystem_but_not_agent(tmp_path):
    store = GrantStore(tmp_path / "grants.json")
    permission = Permission(ask=lambda name, args: False, mode=Mode.AGGRESSIVE, store=store)
    registry = build_registry(tmp_path)
    registry.register(AgentTool(lambda task: "ok"))
    assert permission.check(registry.get("write_file"), {"path": "a.py", "content": "x"}) is None
    assert permission.check(registry.get("agent"), {"task": "x"}).startswith("refused")


def test_saved_grants_reload_from_disk(tmp_path):
    path = tmp_path / "grants.json"
    first = GrantStore(path)
    first.add(default_scope(classify("bash", {"command": "pytest -q"})))

    second = GrantStore(path)
    assert second.match(classify("bash", {"command": "pytest -q"})) is not None
    assert second.match(classify("bash", {"command": "pytest tests/"})) is None
    assert second.match(classify("bash", {"command": "rm -rf /"})) is None


def test_grant_store_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "grants.json"
    path.write_text("{ not json")
    assert GrantStore(path).grants == []


def test_permission_uses_the_real_home_store_by_default(monkeypatch, tmp_path):
    # Guards against accidentally writing grants during ordinary checks.
    monkeypatch.setattr(permissions_mod, "GRANTS_FILE", tmp_path / "p.json")
    permission = Permission(ask=lambda n, a: False)
    permission.check(build_registry(tmp_path).get("write_file"), {"path": "a", "content": "x"})
    assert not (tmp_path / "p.json").exists()
