"""Tests for the skill system."""

import asyncio

import pytest

from coderill.agent import AgentRuntime
from coderill.config import Config
from coderill.permissions import Mode, ReviewManager
from coderill.providers.types import ChatResponse, ChatStreamEvent
from coderill.session import Session, SessionStore
from coderill.skills import SkillCatalog, default_roots
from coderill.tools import build_registry
from coderill.tools.base import ToolError
from coderill.tools.skills import LoadSkillTool


def _write_skill(root, name, description, body="Body text"):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n")


def test_discovers_directory_skills(tmp_path):
    _write_skill(tmp_path, "changelog", "Write a changelog entry")
    catalog = SkillCatalog([tmp_path])
    assert [skill.name for skill in catalog.skills] == ["changelog"]
    assert "Write a changelog entry" in catalog.render()


def test_single_file_skill(tmp_path):
    (tmp_path / "migrate.md").write_text("Do a migration carefully")
    catalog = SkillCatalog([tmp_path])
    assert catalog.skills[0].name == "migrate"
    assert "migration" in catalog.skills[0].description


def test_index_md_is_not_a_skill(tmp_path):
    (tmp_path / "INDEX.md").write_text("just a catalogue")
    assert SkillCatalog([tmp_path]).skills == []


def test_project_root_overrides_global(tmp_path):
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    _write_skill(project, "deploy", "project deploy")
    _write_skill(global_root, "deploy", "global deploy")

    catalog = SkillCatalog([project, global_root])
    assert len(catalog.skills) == 1
    assert catalog.skills[0].description == "project deploy"


def test_load_returns_the_body(tmp_path):
    _write_skill(tmp_path, "x", "d", body="HELLO BODY")
    assert "HELLO BODY" in SkillCatalog([tmp_path]).load("x")


def test_skill_catalog_uses_discovery_snapshot(tmp_path):
    skill = tmp_path / "x.md"
    skill.write_text("original", encoding="utf-8")
    catalog = SkillCatalog([tmp_path])
    skill.write_text("changed", encoding="utf-8")
    assert catalog.load("x") == "original"


def test_skill_snapshot_survives_session_reload_and_fork(tmp_path):
    from coderill.session import Session, SessionStore

    root = tmp_path / "skills"
    root.mkdir()
    skill = root / "x.md"
    skill.write_text("original", encoding="utf-8")
    catalog = SkillCatalog([root])
    store = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), store)
    session.record_skill_catalog([{
        "name": entry.name, "description": entry.description, "path": str(entry.path),
        "digest": store.put_blob(session.id, entry.content.encode("utf-8")),
    } for entry in catalog.skills])
    skill.write_text("changed", encoding="utf-8")
    restored = Session.load(session.id, store)
    assert SkillCatalog.from_snapshot(restored.skill_snapshot, restored).load("x") == "original"
    fork = Session.load(restored.fork(store).id, store)
    assert SkillCatalog.from_snapshot(fork.skill_snapshot, fork).load("x") == "original"


def test_skill_symlink_cannot_escape_discovery_root(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    root = tmp_path / "skills"
    root.mkdir()
    try:
        (root / "escape.md").symlink_to(outside)
    except OSError:
        return  # Windows may disallow symlink creation without developer mode.
    assert SkillCatalog([root]).skills == []


def test_load_skill_tool_rejects_unknown_names(tmp_path):
    _write_skill(tmp_path, "x", "d")
    tool = LoadSkillTool(SkillCatalog([tmp_path]))
    with pytest.raises(ToolError):
        tool.execute("nope")


def test_runtime_exposes_skills_and_the_tool(tmp_path):
    _write_skill(tmp_path / ".agents" / "skills", "demo", "A demo skill")

    config = Config(api_key="x", workspace=tmp_path)
    session = Session.new("m", str(tmp_path), SessionStore(tmp_path / "sessions"))
    registry = build_registry(tmp_path)
    skills = SkillCatalog(default_roots(tmp_path))
    registry.register(LoadSkillTool(skills))

    class Provider:
        def __init__(self):
            self.requests = []

        async def stream(self, request):
            self.requests.append(request)
            yield ChatStreamEvent("completed", response=ChatResponse("done"))

    provider = Provider()
    agent = AgentRuntime(config, session, registry, provider, ReviewManager(tmp_path, mode=Mode.BYPASS), skills=skills)
    assert asyncio.run(agent.run_turn("hello")).status == "complete"

    assert registry.get("load_skill") is not None
    system = provider.requests[0].messages[0].content
    assert "# Skills" in system
    assert "demo: A demo skill" in system
