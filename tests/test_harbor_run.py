import json
from types import SimpleNamespace

from examples import harbor_run
from examples.harbor_run import classify, harbor_command, preflight, summarize


def test_harbor_command_keeps_secret_out_of_arguments(tmp_path):
    env = {"CODERILL_API_KEY": "secret-value", "CODERILL_PROVIDER": "openai", "CODERILL_MODEL": "test-model",
           "CODERILL_THINKING_ENABLED": "0"}
    command = harbor_command("harbor", "terminal-bench@2.0", "chess-best-move", tmp_path, env)
    assert "--include-task-name" in command
    assert "chess-best-move" in command
    assert "secret-value" not in " ".join(command)
    assert "CODERILL_API_KEY=${CODERILL_API_KEY}" in command
    assert "CODERILL_THINKING_ENABLED=${CODERILL_THINKING_ENABLED}" in command


def test_preflight_rejects_missing_daemon_and_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("examples.harbor_run.shutil.which", lambda _: None)
    monkeypatch.setattr("examples.harbor_run.subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    checks = preflight(tmp_path, "harbor", {})
    assert not all(item["ok"] for item in checks)
    assert {item["name"] for item in checks if not item["ok"]} >= {"docker", "harbor", "CODERILL_API_KEY"}


def test_preflight_reads_project_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "CODERILL_API_KEY=test-secret\nCODERILL_MODEL=test-model\nCODERILL_PROVIDER=openai\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harbor_run, "__file__", str(tmp_path / "examples" / "harbor_run.py"))
    for name in ("CODERILL_API_KEY", "CODERILL_MODEL", "CODERILL_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(harbor_run, "preflight", lambda root, harbor, env: [
        {"name": "configured", "ok": all(env.get(name) for name in
         ("CODERILL_API_KEY", "CODERILL_MODEL", "CODERILL_PROVIDER")), "detail": "configured"}
    ])
    assert harbor_run.main(["preflight"]) == 0


def test_harbor_report_separates_reward_and_infrastructure(tmp_path):
    cases = {
        "pass": {"verifier_result": {"rewards": {"reward": 1}}},
        "fail": {"verifier_result": {"rewards": {"reward": 0}}},
        "timeout": {"exception_info": {"exception_type": "AgentTimeoutError"}},
        "setup": {"environment_setup": {"finished_at": "now"}},
        "missing": {phase: {"finished_at": "now"} for phase in
                    ("environment_setup", "agent_setup", "agent_execution", "verifier")},
    }
    for name, result in cases.items():
        folder = tmp_path / "job" / "trials" / name
        folder.mkdir(parents=True)
        (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (tmp_path / "job" / "result.json").write_text(json.dumps({"summary": "job"}), encoding="utf-8")
    report = summarize(tmp_path)
    assert report["total"] == 5
    assert report["explicit_rewards"] == 2
    assert report["outcomes"] == {"reward_zero": 1, "reward_missing": 1, "passed": 1,
                                  "agent_setup": 1, "timeout": 1}
    assert classify({"verifier_result": {"rewards": {"reward": False}}}) == "environment_setup"


def test_harbor_report_reads_current_trial_layout_and_setup_error(tmp_path):
    trial = tmp_path / "job" / "chess-best-move__abc"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps({
        "trial_name": trial.name,
        "exception_info": {"exception_type": "RuntimeError"},
        "environment_setup": {"started_at": "now", "finished_at": "now"},
        "agent_setup": None,
    }), encoding="utf-8")
    (trial.parent / "result.json").write_text(json.dumps({"n_total_trials": 1}), encoding="utf-8")
    report = summarize(tmp_path)
    assert report["total"] == 1
    assert report["outcomes"] == {"environment_setup": 1}
    assert classify({"exception_info": {"exception_type": "NonZeroAgentExitCodeError"},
                     "agent_setup": {"started_at": "now"},
                     "agent_execution": {"started_at": "now"},
                     "verifier": {"started_at": "now"},
                     "verifier_result": {"rewards": {"reward": 0}}}) == "agent_execution"


def test_harbor_run_fails_when_trial_failed_despite_zero_cli_status(tmp_path, monkeypatch):
    monkeypatch.setattr(harbor_run, "preflight", lambda *_: [{"name": "ready", "ok": True, "detail": "ready"}])
    monkeypatch.setattr(harbor_run, "harbor_command", lambda *_: ["harbor"])
    monkeypatch.setattr(harbor_run.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(harbor_run, "summarize", lambda *_: {"total": 1, "outcomes": {"environment_setup": 1}})
    assert harbor_run.main(["run", "--output", str(tmp_path)]) == 1
