"""Capability retrieval keeps optional embeddings from blocking local search."""

import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from coderill.routing import search_capabilities


def test_local_search_handles_chinese_and_stable_ties():
    items = [
        {"name": "documents", "description": "搜索文档"},
        {"name": "calendar", "description": "搜索会议"},
        {"name": "other", "description": "unrelated"},
    ]
    assert [item["name"] for item in search_capabilities("搜索文档", items)] == ["documents", "calendar"]
    assert search_capabilities("missing", items) == []
    with pytest.raises(ValueError, match="blank"):
        search_capabilities("  ", items)


def test_semantic_search_uses_bounded_text_and_falls_back(monkeypatch):
    from coderill import routing

    items = [{"name": "calendar", "description": "meetings"},
             {"name": "documents", "description": "files"}]
    seen = []

    class Embeddings:
        def create(self, *, model, input):
            seen.append(input)
            return SimpleNamespace(data=[
                SimpleNamespace(index=2, embedding=[1.0, 0.0]),
                SimpleNamespace(index=0, embedding=[1.0, 0.0]),
                SimpleNamespace(index=1, embedding=[0.0, 1.0]),
            ])

    monkeypatch.setenv("ROUTING_TEST_KEY", "unused")
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: SimpleNamespace(embeddings=Embeddings()))
    config = {"api_key_env": "ROUTING_TEST_KEY", "base_url": "https://example.com", "model": "embed"}
    assert [item["name"] for item in search_capabilities("x" * 3000, items, config)] == ["documents", "calendar"]
    assert len(seen[0][0]) == 2000

    def broken(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(routing, "_semantic_rank", broken)
    assert [item["name"] for item in search_capabilities("meetings", items, config)] == ["calendar"]


def test_expired_turn_budget_skips_external_embedding(monkeypatch):
    monkeypatch.setenv("ROUTING_TEST_KEY", "unused")
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: pytest.fail("external call after deadline"))
    items = [{"name": "calendar", "description": "meetings"}]
    config = {"api_key_env": "ROUTING_TEST_KEY", "base_url": "https://example.com", "model": "embed"}
    assert search_capabilities("meetings", items, config, deadline=time.monotonic() - 1) == items


def test_qualified_light_model_requires_matching_report(tmp_path):
    from coderill.config import Config
    from coderill.routing import light_profile_hash, qualified_light_config

    dataset = tmp_path / "cases.json"
    dataset.write_text("[]", encoding="utf-8")
    profile = {"model": "small", "provider": "openai", "vision": False, "max_context_tokens": 32000}
    report = {"status": "passed", "provider": "openai", "base_url": "", "primary_model": "large",
              "light_model": "small", "dataset": str(dataset),
              "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
              "profile_sha256": "",
              "read_only_count": 100, "boundary_count": 50,
              "primary_pass_rate": 0.90, "light_pass_rate": 0.89,
              "primary_boundary_error_rate": 0.04, "light_boundary_error_rate": 0.04}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    config = Config(api_key="unused", model="large", workspace=tmp_path,
                    profiles={"small": profile},
                    routing={"enabled": True, "light_profile": "small", "qualification_report": str(path)})
    report["profile_sha256"] = light_profile_hash(profile, config)
    path.write_text(json.dumps(report), encoding="utf-8")
    assert qualified_light_config(config).model == "small"
    from dataclasses import replace

    assert qualified_light_config(replace(config, temperature=0.9)) is None
    report["light_pass_rate"] = 0.80
    path.write_text(json.dumps(report), encoding="utf-8")
    assert qualified_light_config(config) is None
    report["light_pass_rate"] = 0.89
    path.write_text(json.dumps(report), encoding="utf-8")
    dataset.write_text("[1]", encoding="utf-8")
    assert qualified_light_config(config) is None


def test_routing_events_and_model_usage_survive_replay(tmp_path):
    from coderill.providers.openai_compatible import session_cost
    from coderill.session import Session, SessionStore

    store = SessionStore(tmp_path / "sessions")
    session = Session.new("large", str(tmp_path), store)
    session.record_usage(100, 25, provider="openai", model="large")
    session.record_usage(50, 10, provider="openai", model="small", kind="task_boundary")
    session.record_routing(stage="task_boundary", candidates=["small", "large"], selected="small", model="small")
    loaded = Session.load(session.id, store)
    assert loaded.usage == {"requests": 2, "input_tokens": 150, "output_tokens": 35}
    assert loaded.usage_by_model["openai/small"]["requests"] == 1
    assert loaded.routing_events[0]["selected"] == "small"
    fork = loaded.fork()
    assert fork.usage["requests"] == 0
    assert fork.usage_by_model == {}
    assert fork.routing_events == []
    assert session_cost({"unknown": {"requests": 1, "input_tokens": 1, "output_tokens": 1}})[1] is False


def test_skill_search_reaches_entries_hidden_by_prompt_budget(tmp_path):
    from coderill.skills import Skill, SkillCatalog
    from coderill.tools.skills import SkillSearchTool

    catalog = SkillCatalog([])
    catalog.skills = [Skill(f"skill_{index}", "other task", tmp_path / "SKILL.md", "body")
                      for index in range(100)]
    catalog.skills.append(Skill("z_target", "search documents 搜索文档", tmp_path / "SKILL.md", "body"))
    assert "z_target" not in catalog.render(budget=100)
    matches = json.loads(SkillSearchTool(catalog).execute("搜索文档"))["matches"]
    assert matches[0]["name"] == "z_target"
