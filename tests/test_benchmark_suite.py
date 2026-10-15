"""Reproducible report accounting without external model calls."""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from coderill.config import Config
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
