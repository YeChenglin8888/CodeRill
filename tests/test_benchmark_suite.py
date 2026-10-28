"""Reproducible report accounting without external model calls."""

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from coderill.config import Config
from coderill.harness_tree import base_harness, edit_harness
from coderill.tools import Registry
from examples import benchmark_suite
from examples.benchmark_suite import run_case, summarize


def test_summary_keeps_unverified_and_infrastructure_errors_out_of_passes():
    report = summarize([
        {"outcome": "passed"}, {"outcome": "verification_failed"},
        {"outcome": "unverified"}, {"outcome": "infrastructure_error"},
    ])
    assert report["tasks"] == 4
    assert report["passed"] == 1
    assert report["pass_rate"] == 0.25
    assert report["outcomes"]["infrastructure_error"] == 1


def test_invalid_verifier_fails_before_any_agent_run(tmp_path):
    with pytest.raises(ValueError, match="verify commands"):
        run_case({"id": "case", "workspace": "missing", "prompt": "fix", "verify": ["pytest -q"]}, tmp_path)


def test_case_runs_in_copy_and_counts_verified_result(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "answer.txt").write_text("before", encoding="utf-8")
    monkeypatch.setattr(Config, "from_env", classmethod(
        lambda cls, workspace, **_kwargs: cls(api_key="unused", workspace=workspace)))

    class FakeState:
        def __init__(self, config):
            self.service_processes = {}
            async def run_turn(_prompt):
                (config.workspace / "answer.txt").write_text("after", encoding="utf-8")
                return SimpleNamespace(status="complete", error="")

            self.agent = SimpleNamespace(run_turn=run_turn, on_event=None)

        def run_async(self, coroutine):
            return asyncio.run(coroutine)

        def close(self):
            pass

    monkeypatch.setattr(benchmark_suite, "build_state", lambda config, *_args, **_kwargs: FakeState(config))
    case = {"id": "copy", "workspace": "source", "prompt": "change file",
            "verify": [[sys.executable, "-c", "from pathlib import Path; assert Path('answer.txt').read_text() == 'after'"]]}
    result = run_case(case, tmp_path)
    assert result["outcome"] == "passed"
    assert (source / "answer.txt").read_text(encoding="utf-8") == "before"
    assert result["approval_policy"] == "bypass"
    assert result["settings"]["max_turns"] == 30
    assert result["case_metadata"]["prompt"] == "change file"


def test_case_evaluates_selected_harness_in_fresh_workspace(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "answer.txt").write_text("before", encoding="utf-8")
    monkeypatch.setattr(Config, "from_env", classmethod(
        lambda cls, workspace, **_kwargs: cls(api_key="unused", workspace=workspace)))
    monkeypatch.setattr("coderill.harness_tree.default_roots", lambda _workspace: [])

    class FakeState:
        def __init__(self, config):
            self.service_processes = {}
            self.agent = SimpleNamespace(harness_overlay="", registry=Registry(config.workspace), on_event=None)

            async def run_turn(_prompt):
                assert set(self.agent.registry.names()) == ({
                    "read_file", "write_file", "edit_file", "read_multi", "glob", "grep"}
                        | ({"load_skill", "skill_search"} if "Use boundary tests" in self.agent.harness_overlay else set()))
                if "Use boundary tests" in self.agent.harness_overlay:
                    (config.workspace / "answer.txt").write_text("after", encoding="utf-8")
                return SimpleNamespace(status="complete", error="")

            self.agent.run_turn = run_turn

        def run_async(self, coroutine):
            return asyncio.run(coroutine)

        def close(self):
            pass

    monkeypatch.setattr(benchmark_suite, "build_state", lambda config, *_args, **_kwargs: FakeState(config))
    case = {"id": "copy", "workspace": "source", "prompt": "change file", "verify": [
        [sys.executable, "-c", "from pathlib import Path; assert Path('answer.txt').read_text() == 'after'"]]}
    harness = edit_harness(base_harness(), {"system_prompt": "Use boundary tests",
                                            "skills": {"check": "# Check\nRun boundary tests"}})
    assert run_case(case, tmp_path, no_docker=True)["outcome"] == "verification_failed"
    assert run_case(case, tmp_path, harness=harness, no_docker=True)["outcome"] == "passed"
    assert (source / "answer.txt").read_text(encoding="utf-8") == "before"


@pytest.mark.parametrize("outcome", ["passed", "verification_failed", "unverified", "agent_failed", "infrastructure_error"])
def test_cli_writes_report_and_fails_gate_unless_every_case_passes(tmp_path, monkeypatch, outcome):
    manifest = tmp_path / "suite.json"
    manifest.write_text(json.dumps([{"id": "sample", "workspace": "source", "prompt": "fix"}]), encoding="utf-8")
    output = tmp_path / "results" / "run.json"
    monkeypatch.setattr(sys, "argv", ["benchmark_suite", str(manifest), "--output", str(output)])

    def run(*_args, **_kwargs):
        if outcome == "infrastructure_error":
            raise RuntimeError("setup unavailable")
        return {"id": "sample", "outcome": outcome}

    monkeypatch.setattr(benchmark_suite, "run_case", run)
    if outcome == "passed":
        benchmark_suite.main()
    else:
        with pytest.raises(SystemExit) as exc:
            benchmark_suite.main()
        assert exc.value.code == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert len(report["manifest_sha256"]) == 64
    assert report["summary"]["all_passed"] is (outcome == "passed")
    assert report["summary"]["outcomes"] == {outcome: 1}


def test_cli_can_evaluate_one_holdout_case(tmp_path, monkeypatch):
    manifest = tmp_path / "suite.json"
    manifest.write_text(json.dumps([
        {"id": "train", "workspace": "source", "prompt": "train"},
        {"id": "holdout", "workspace": "source", "prompt": "holdout"}]), encoding="utf-8")
    output = tmp_path / "report.json"
    seen = []
    monkeypatch.setattr(benchmark_suite, "run_case", lambda case, *_args, **_kwargs: (
        seen.append(case["id"]) or {"id": case["id"], "outcome": "passed"}))
    monkeypatch.setattr(sys, "argv", ["benchmark_suite", str(manifest), "--case-id", "holdout",
                                    "--output", str(output)])
    benchmark_suite.main()
    assert seen == ["holdout"]
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["tasks"] == 1
