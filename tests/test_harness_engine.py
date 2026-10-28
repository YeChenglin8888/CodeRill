"""Harness² recursion, component binding and optional Jev decisions."""

import asyncio

import httpx
import pytest

from coderill.agent.runtime import AgentRuntime
from coderill.config import Config
from coderill.harness_engine import run_harness
from coderill.harness_tree import base_harness, bind_harness, edit_harness
from coderill.jev import choose
from coderill.permissions import Mode, ReviewManager
from coderill.session import Session, SessionStore
from coderill.tools import Registry, build_registry


def test_components_bind_to_runtime_without_host_hooks(tmp_path):
    tree = edit_harness(base_harness(), {
        "system_prompt": "Check the result", "guardrails": "Never overwrite inputs",
        "memory": "The task needs a parser", "tool_guidance": "Read before edit",
        "skills": {"parser": "# Parser\nUse the parser workflow."},
        "scripts": {"check": "print('checked')\n"},
        "subagents": {"reader": "Inspect the format"},
        "middleware": {"blocked_tools": ["bash", "bash"]},
    })
    overlay, catalog, hooks = bind_harness(tree, tmp_path)
    assert all(text in overlay for text in ("Check the result", "Never overwrite inputs",
                                          "The task needs a parser", "Read before edit",
                                          "reader: Inspect the format", "check.py"))
    assert catalog.load("parser").startswith("# Parser")
    assert (tmp_path / ".harness2" / "scripts" / "check.py").read_text() == "print('checked')\n"
    assert hooks.run_pre("bash", {}) and hooks.run_pre("read_file", {}) is None
    assert hooks
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    runtime = AgentRuntime(Config(api_key="unused", workspace=tmp_path), session, Registry(tmp_path),
                           object(), ReviewManager(tmp_path, mode=Mode.BYPASS), hooks=hooks)
    assert runtime.hooks is hooks
    with pytest.raises(ValueError, match="invalid scripts name"):
        edit_harness(tree, {"scripts": {"../escape": "print(1)"}})
    with pytest.raises(SyntaxError):
        edit_harness(tree, {"scripts": {"bad": "def ="}})


@pytest.mark.parametrize("mode", ["seq", "agg"])
def test_four_k_rollouts_with_cited_composition_and_anonymous_selection(mode):
    calls = []
    seen = {}

    async def probe(label, tree):
        calls.append((label, tree["system_prompt"]))
        return {"status": "complete", "trace": f"{label} checked the task inputs",
                "text": f"{label} delivered a result", "changes": f"{label} changed output",
                "workspace": f"/tmp/{label}"}

    async def editor(stage, evidence):
        if stage == "propose":
            return {"patch": {"system_prompt": evidence["dimension"] + str(evidence["index"])}}
        if stage == "compose":
            index = evidence["index"]
            if mode == "agg":
                assert set(evidence["candidates"]) == {"inv1", "dom1", "inv2", "dom2"}
            base = "base1" if mode == "agg" or index == 1 else f"c{index - 1}"
            return {"patch": {"system_prompt": f"composed{index}"}, "citations": {
                base: f"{base} checked the task inputs",
                f"inv{index}": f"inv{index} checked the task inputs"}}
        if stage == "reflect":
            label = evidence["selection"]["choice"]
            return {"cue": "When tasks need checks", "lesson": "Inspect the task inputs before finalizing.",
                    "support": f"{label} checked the task inputs"}
        raise AssertionError(stage)

    async def selector(_task, candidates):
        seen.update(candidates)
        return {"choice": "B"}

    result = asyncio.run(run_harness("task", probe, editor, [], mode=mode, k=2, selector=selector))
    assert len(calls) == 8
    assert set(result["runs"]) == ({"base1", "base2", "inv1", "dom1", "inv2", "dom2"} |
                                    ({"c1", "c2"} if mode == "seq" else {"agg1", "agg2"}))
    assert set(seen) == {"A", "B"}
    assert all("harness" not in artifact for artifact in seen.values())
    assert result["selection"]["anonymous_choice"] == "B"
    assert result["experience"]["harness"]["system_prompt"] in {"composed1", "composed2"}
    if mode == "seq":
        assert ("c1", "composed1") in calls
    else:
        assert ("agg1", "composed1") in calls


def test_new_task_reuses_only_selected_components():
    prior = {"harness": edit_harness(base_harness(), {"system_prompt": "old prompt",
        "skills": {"check": "# Check\nVerify."}}), "lesson": "Check files"}
    bases = []

    async def probe(label, tree):
        if label.startswith("base"):
            bases.append(tree)
        return {"status": "complete", "trace": f"{label} checked current task", "text": label}

    async def editor(stage, evidence):
        if stage == "reuse":
            return {"index": 0, "components": ["skills"]}
        if stage == "propose":
            return {"patch": {"tool_guidance": "inspect"}}
        if stage == "compose":
            return {"patch": {}, "citations": {"base1": "base1 checked current task",
                                               "inv1": "inv1 checked current task"}}
        return {}

    asyncio.run(run_harness("new task", probe, editor, [prior]))
    assert bases[0]["skills"] == prior["harness"]["skills"]
    assert bases[0]["system_prompt"] == ""

    bases.clear()

    async def jev_reuse(_task, _library):
        return {"index": None, "decision": {"choice": "none"}}

    result = asyncio.run(run_harness("unrelated task", probe, editor, [prior],
                                     reuse_selector=jev_reuse))
    assert bases[0]["skills"] == {}
    assert result["reuse_decision"]["decision"]["choice"] == "none"


def test_new_task_can_compose_distinct_components_from_prior_experiences():
    prior = [
        {"harness": edit_harness(base_harness(), {"skills": {"check": "# Check\nVerify."}})},
        {"harness": edit_harness(base_harness(), {"tool_guidance": "Read first"})},
    ]
    bases = []

    async def probe(label, tree):
        if label == "base1":
            bases.append(tree)
        return {"status": "complete", "trace": f"{label} checked current task"}

    async def editor(stage, _evidence):
        if stage == "reuse":
            return {"entries": [{"index": 0, "components": ["skills"]},
                                {"index": 1, "components": ["tool_guidance"]}]}
        if stage == "propose":
            return {"patch": {}}
        if stage == "compose":
            return {"patch": {}, "citations": {"base1": "base1 checked current task",
                                               "inv1": "inv1 checked current task"}}
        return {}

    asyncio.run(run_harness("task", probe, editor, prior))
    assert bases[0]["skills"] == prior[0]["harness"]["skills"]
    assert bases[0]["tool_guidance"] == "Read first"


def test_uncited_composition_retries_then_keeps_current_harness():
    attempts = []

    async def probe(label, tree):
        return {"status": "complete", "trace": f"{label} inspected inputs",
                "text": label, "harness": tree}

    async def editor(stage, evidence):
        if stage == "propose":
            return {"patch": {"system_prompt": "new guidance"}}
        if stage == "compose":
            attempts.append(evidence["validation_error"])
            return {"patch": {"system_prompt": "unverified guidance"},
                    "citations": {"base1": "invented evidence", "inv1": "invented evidence"}}
        return {}

    result = asyncio.run(run_harness("task", probe, editor, []))
    assert len(attempts) == 2 and "exact excerpts" in attempts[1]
    assert result["finals"][0]["fallback"]
    assert result["harnesses"]["c1"] == base_harness()


@pytest.mark.parametrize("passing", ["inv1", None])
def test_verification_selects_passing_candidate_and_rejects_unverified_experience(passing):
    async def probe(label, tree):
        return {"status": "complete", "trace": f"{label} checked the task inputs",
                "text": f"{label} delivered a result", "verification": {"passed": label == passing, "checks": []}}

    async def editor(stage, evidence):
        if stage == "propose":
            return {"patch": {"system_prompt": evidence["dimension"]}}
        if stage == "compose":
            return {"patch": {"system_prompt": "combined"}, "citations": {
                "base1": "base1 checked the task inputs", "inv1": "inv1 checked the task inputs"}}
        if stage == "reflect":
            return {"cue": "When task checks fail", "lesson": "Run the boundary checks before finishing.",
                    "support": "inv1 checked the task inputs"}
        raise AssertionError(stage)

    result = asyncio.run(run_harness("task", probe, editor, []))
    assert result["selection"]["choice"] == ("inv1" if passing else "c1")
    assert result["selection"]["verified"] is bool(passing)
    assert (result["experience"] is not None) is bool(passing)


def test_verification_keeps_passing_baseline_without_learning_from_regressions():
    async def probe(label, tree):
        return {"status": "complete", "trace": f"{label} checked the task inputs",
                "verification": {"passed": label == "base1", "checks": []}}

    async def editor(stage, evidence):
        if stage == "propose":
            return {"patch": {"system_prompt": evidence["dimension"]}}
        if stage == "compose":
            return {"patch": {"system_prompt": "combined"}, "citations": {
                "base1": "base1 checked the task inputs", "inv1": "inv1 checked the task inputs"}}
        raise AssertionError("baseline needs no reflection")

    result = asyncio.run(run_harness("task", probe, editor, []))
    assert result["selection"]["choice"] == "base1"
    assert result["selection"]["verified"] is True
    assert result["experience"] is None


def test_jev_choice_schema_and_opt_in_registry(tmp_path, monkeypatch):
    sent = {}

    class Client:
        def __init__(self, **kwargs):
            sent["headers"] = kwargs["headers"]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, url, *, json, timeout):
            sent.update({"url": url, "request": json, "timeout": timeout})
            return httpx.Response(200, request=httpx.Request("POST", url), json={"model": "jev-1.14.0", "answers": {"decision": {
                "type": "choice", "choice": "B", "probabilities": {"A": 0.2, "B": 0.8},
                "confidence": 0.8}}, "usage": {"input_tokens": 20, "output_tokens": 2}})

    monkeypatch.setattr(httpx, "Client", Client)
    answer = choose("observed output", "Which is better?", {"A": "first", "B": "second"}, key="secret")
    assert answer["choice"] == "B"
    assert sent["request"]["questions"]["decision"]["criteria"] == {"A": "first", "B": "second"}
    assert sent["headers"]["Authorization"] == "Bearer secret"
    with pytest.raises(ValueError, match="2-8"):
        choose("state", "question", {"A": "only one"}, key="secret")
    disabled = Config(api_key="unused", workspace=tmp_path)
    enabled = Config(api_key="unused", workspace=tmp_path, jev_decisions_enabled=True,
                     typesafe_api_key="secret")
    assert "jev_decide" not in build_registry(tmp_path, config=disabled).names()
    decision_tool = build_registry(tmp_path, config=enabled).get("jev_decide")
    assert decision_tool and decision_tool.needs_consent
