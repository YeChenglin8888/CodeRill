"""The improvement harness persists audited component trees between tasks."""

import asyncio
import json

import pytest

from coderill.agent.runtime import AgentRuntime
from coderill.config import Config
from coderill.harness_cli import _reuse_options
from coderill.harness_library import ImprovementLibrary
from coderill.harness_tree import HarnessHooks, base_harness, diff_harness, edit_harness, read_harness, write_harness
from coderill.permissions import Mode, ReviewManager
from coderill.providers.types import ChatResponse, ChatStreamEvent, ToolCall
from coderill.session import Session, SessionStore
from coderill.tools import build_registry


def test_component_tree_roundtrip_and_declarative_plugin(tmp_path):
    tree = edit_harness(base_harness(), {
        "system_prompt": "Inspect the output", "guardrails": "Keep inputs intact",
        "memory": "The input is CSV", "tool_guidance": "Read before editing",
        "skills": {"check": "# Check\nRun the verifier"},
        "scripts": {"verify": "print('valid')\n"},
        "subagents": {"reader": "Inspect the format"},
        "middleware": {"pre_rules": [{"tool": "read_file", "argument_contains": "secret.txt",
                                      "message": "do not open secrets"}],
                       "post_rules": [{"tool": "read_file", "result_contains": "ERROR",
                                       "message": "inspect the failing line"}]},
    })
    root = write_harness(tree, tmp_path / "harness")
    assert read_harness(root) == tree
    assert (root / "systemprompt.md").read_text() == "Inspect the output"
    assert (root / "skills" / "check" / "SKILL.md").is_file()
    assert (root / "scripts" / "verify.py").is_file()
    assert (root / "sub_agents" / "reader" / "AGENT.md").is_file()
    assert json.loads((root / "plugin" / "tool_loop.json").read_text())["pre_rules"]
    assert diff_harness(base_harness(), tree)["added"] == [
        "scripts/verify.py", "skills/check/SKILL.md", "sub_agents/reader/AGENT.md"]
    hooks = HarnessHooks(tree["middleware"])
    assert hooks.run_pre("read_file", {"path": "secret.txt"}) == "do not open secrets"
    assert hooks.run_pre("read_file", {"path": "ordinary.txt"}) is None
    assert hooks.run_post("read_file", {}, "ERROR at line 3").endswith("inspect the failing line")
    with pytest.raises(FileExistsError):
        write_harness(tree, root)
    (root / "unexpected.py").write_text("pass")
    with pytest.raises(ValueError, match="off-surface"):
        read_harness(root)


def test_improvement_library_persists_full_tree_and_scopes_domain(tmp_path):
    library = ImprovementLibrary(tmp_path / "library", "project-a", "parser")
    harness = edit_harness(base_harness(), {"skills": {"parse": "# Parse\nCheck columns"}})
    experience = {"cue": "CSV parsing work", "lesson": "Check expected columns before parsing.",
                  "support": "final run checked the CSV columns", "edits": "Added a parser skill",
                  "harness": harness}
    path = library.add(experience, selection={"choice": "c1"}, result=tmp_path / "result.json")
    assert (path / "EXPERIENCE.md").is_file()
    assert read_harness(path / "harness") == harness
    assert "CSV parsing work" in (library.root / "INDEX.md").read_text()
    assert library.read()[0]["harness"] == harness
    assert ImprovementLibrary(tmp_path / "library", "project-a", "other").read() == []
    assert ImprovementLibrary(tmp_path / "library", "project-b", "parser").read() == []


def test_legacy_jsonl_experience_can_be_reused(tmp_path):
    path = tmp_path / "legacy.jsonl"
    path.write_text(json.dumps({"project": "p", "overlay": "old hint", "cue": "old"}) + "\n")
    assert ImprovementLibrary(path, "p").read()[0]["harness"]["system_prompt"] == "old hint"


def test_jev_shortlist_stays_within_choice_limit():
    entries = [{"cue": "unrelated", "lesson": "old"} for _ in range(10)]
    entries[0]["cue"] = "parser failure"
    options = _reuse_options("parser failure", entries)
    assert len(options) == 8
    assert "E0" in options
    assert "E1" not in options


def test_plugin_changes_actual_tool_results(tmp_path):
    (tmp_path / "ordinary.txt").write_text("ERROR at line 3")
    (tmp_path / "secret.txt").write_text("private")
    middleware = edit_harness(base_harness(), {"middleware": {
        "pre_rules": [{"tool": "read_file", "argument_contains": "secret.txt", "message": "blocked secret"}],
        "post_rules": [{"tool": "read_file", "result_contains": "ERROR", "message": "check line 3"}],
    }})["middleware"]

    class Provider:
        def __init__(self):
            self.responses = [ChatResponse("", (ToolCall("a", "read_file", {"path": "secret.txt"}),
                                               ToolCall("b", "read_file", {"path": "ordinary.txt"}))),
                              ChatResponse("done")]

        async def stream(self, _request):
            yield ChatStreamEvent("completed", response=self.responses.pop(0))

    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    runtime = AgentRuntime(Config(api_key="unused", model="fake", workspace=tmp_path), session,
                           build_registry(tmp_path), Provider(), ReviewManager(tmp_path, mode=Mode.BYPASS),
                           hooks=HarnessHooks(middleware))
    assert asyncio.run(runtime.run_turn("inspect")).status == "complete"
    assert session.finished_calls["a"]["status"] == "denied"
    assert "private" not in session.finished_calls["a"]["content"]
    assert "check line 3" in session.finished_calls["b"]["content"]
