"""Tests for the skill system."""

import pytest

import coderill.session as session_mod
from coderill.agent import Agent
from coderill.config import Config
from coderill.permissions import Permission
from coderill.session import Session
from coderill.skills import SkillCatalog, default_roots
from coderill.tools import build_registry
from coderill.tools.base import ToolError
from coderill.tools.skills import LoadSkillTool


def _write_skill(root, name, description, body="Body text"):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n")


class _FakeLLM:
    def stream(self, messages, tools=None, deadline=None):
        return iter(())


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


def test_load_skill_tool_rejects_unknown_names(tmp_path):
    _write_skill(tmp_path, "x", "d")
    tool = LoadSkillTool(SkillCatalog([tmp_path]))
    with pytest.raises(ToolError):
        tool.execute("nope")


def test_agent_exposes_skills_and_the_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSION_DIR", tmp_path)
    _write_skill(tmp_path / ".agents" / "skills", "demo", "A demo skill")

    config = Config(api_key="x", workspace=tmp_path)
    session = Session.new("m", str(tmp_path))
    registry = build_registry(tmp_path)
    agent = Agent(
        config,
        session,
        registry,
        llm=_FakeLLM(),
        permissions=Permission(allow_all=True),
        skills=SkillCatalog(default_roots(tmp_path)),
    )

    assert registry.get("load_skill") is not None
    system = agent._outgoing_messages()[0]["content"]
    assert "# Skills" in system
    assert "demo: A demo skill" in system


def test_agent_without_skills_registers_no_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSION_DIR", tmp_path)
    config = Config(api_key="x", workspace=tmp_path)
    session = Session.new("m", str(tmp_path))
    registry = build_registry(tmp_path)
    Agent(config, session, registry, llm=_FakeLLM(), permissions=Permission(allow_all=True), skills=SkillCatalog([]))
    assert registry.get("load_skill") is None
