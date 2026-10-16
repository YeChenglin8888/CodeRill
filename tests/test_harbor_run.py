import json

from examples.harbor_run import classify, harbor_command, preflight, summarize


def test_harbor_command_keeps_secret_out_of_arguments(tmp_path):
    env = {"CODERILL_API_KEY": "secret-value", "CODERILL_PROVIDER": "openai", "CODERILL_MODEL": "test-model"}
    command = harbor_command("harbor", "terminal-bench@2.0", "chess-best-move", tmp_path, env)
    assert "--include-task-name" in command
    assert "chess-best-move" in command
    assert "secret-value" not in " ".join(command)
    assert "CODERILL_API_KEY=${CODERILL_API_KEY}" in command


def test_preflight_rejects_missing_daemon_and_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("examples.harbor_run.shutil.which", lambda _: None)
    monkeypatch.setattr("examples.harbor_run.importlib.util.find_spec", lambda _: None)
    monkeypatch.setattr("examples.harbor_run.subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    checks = preflight(tmp_path, "harbor", {})
    assert not all(item["ok"] for item in checks)
    assert {item["name"] for item in checks if not item["ok"]} >= {"docker", "harbor", "CODERILL_API_KEY"}


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
        folder = tmp_path / name
        folder.mkdir()
        (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")
    report = summarize(tmp_path)
    assert report["total"] == 5
    assert report["explicit_rewards"] == 2
    assert report["outcomes"] == {"reward_zero": 1, "reward_missing": 1, "passed": 1,
                                  "agent_setup": 1, "timeout": 1}
    assert classify({"verifier_result": {"rewards": {"reward": False}}}) == "environment_setup"
