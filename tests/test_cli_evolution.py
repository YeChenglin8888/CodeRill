"""Interactive Harness² gates adoption on independent evaluation."""

import io
import json
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from coderill.app import evolution
from coderill.harness_tree import base_harness, edit_harness, write_harness
from coderill.hooks import CombinedHooks, Hooks
from coderill.tools import Registry


def test_cli_evolution_tracks_feedback_evaluates_then_adopts(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "answer.py").write_text("wrong", encoding="utf-8")
    holdout = tmp_path / "holdout"
    holdout.mkdir()
    manifest = tmp_path / "holdout.json"
    manifest.write_text(json.dumps([{"id": "other", "workspace": "holdout", "prompt": "fix another task"}]),
                        encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    console = Console(file=io.StringIO(), force_terminal=False)
    workflow = evolution.EvolutionSession(workspace)
    workflow.track(console)
    (workspace / "answer.py").write_text("fixed by ordinary chat", encoding="utf-8")
    workflow.record_task("fix answer.py", console)
    workflow.feedback = "edge case still fails"
    tree = edit_harness(base_harness(), {"system_prompt": "Check edge cases"})

    base_outcome = "verification_failed"

    def run(command, _console):
        if "coderill.harness2" in command:
            assert "--no-docker" in command and "--defer-library" in command
            assert command[command.index("--feedback") + 1] == "edge case still fails"
            snapshot = Path(command[command.index("--source-snapshot") + 1])
            assert (snapshot / "answer.py").read_text(encoding="utf-8") == "wrong"
            output = Path(command[command.index("--output") + 1])
            write_harness(tree, output / "harnesses" / "inv1")
            result = {"project": "same-project", "selection": {"choice": "inv1", "verified": True},
                      "runs": {name: {"status": "complete", "verification": {"passed": name == "inv1"}}
                               for name in ("base1", "inv1", "dom1", "c1")},
                      "experience": {"harness": tree, "cue": "When fixing edge cases",
                                     "lesson": "Check boundary behavior before finalizing code.",
                                     "support": "inv1 checked boundary behavior"}}
            (output / "result.json").write_text(json.dumps(result), encoding="utf-8")
            return 0
        report = Path(command[command.index("--output") + 1])
        outcome = "passed" if "--harness" in command else base_outcome
        report.write_text(json.dumps({"results": [{"id": "other", "outcome": outcome}]}), encoding="utf-8")
        return 0 if outcome == "passed" else 1

    monkeypatch.setattr(evolution, "_stream", run)
    workflow.refine("python -m pytest -q", console)
    assert workflow.candidate is not None
    assert not (home / ".coderill" / "improvements" / "same-project" / "code" / "entries.jsonl").exists()
    state = SimpleNamespace(config=SimpleNamespace(workspace=workspace),
                            session=SimpleNamespace(id="session"), hooks=Hooks([], []),
                            agent=SimpleNamespace(registry=Registry(workspace)), subagent_services={},
                            rebuild=lambda: None)
    try:
        workflow.adopt(state, console)
    except ValueError as exc:
        assert "独立评测" in str(exc)
    else:
        raise AssertionError("adoption was allowed before holdout")
    base_outcome = "infrastructure_error"
    workflow.evaluate(manifest, "other", console)
    assert not workflow.evaluated
    base_outcome = "verification_failed"
    workflow.evaluate(manifest, "other", console)
    assert workflow.evaluated
    workflow.adopt(state, console)
    assert "Check edge cases" in state.agent.harness_overlay
    assert evolution.active_marker(workspace).is_file()
    assert (home / ".coderill" / "improvements" / "same-project" / "code" / "entries.jsonl").is_file()
    assert evolution.apply_active_harness(state, console)
    assert "Check edge cases" in state.agent.harness_overlay


def test_new_task_discards_old_tracking(tmp_path):
    console = Console(file=io.StringIO(), force_terminal=False)
    workflow = evolution.EvolutionSession(tmp_path)
    workflow.track(console)
    workflow.record_task("first", console)
    workflow.feedback = "failed"
    workflow.record_task("second", console)
    assert workflow.snapshot is None and workflow.task == workflow.feedback == ""


def test_combined_hooks_preserve_user_veto_and_harness_hint():
    class UserHooks(Hooks):
        def __init__(self):
            self.posts = []

        def run_pre(self, name, _input):
            return "user veto" if name == "blocked" else None

        def run_post(self, name, _input, result):
            self.posts.append((name, result))

    class CandidateHooks(UserHooks):
        def run_pre(self, _name, _input):
            return "harness veto"

        def run_post(self, _name, _input, result):
            return result + " hint"

    user = UserHooks()
    hooks = CombinedHooks(user, CandidateHooks())
    assert hooks.run_pre("blocked", {}) == "user veto"
    assert hooks.run_pre("other", {}) == "harness veto"
    assert hooks.run_post("read_file", {}, "result") == "result hint"
    assert user.posts == [("read_file", "result")]


def test_manual_candidate_requires_new_holdout(tmp_path):
    console = Console(file=io.StringIO(), force_terminal=False)
    workflow = evolution.EvolutionSession(tmp_path)
    workflow.run_dir = tmp_path / "run"
    output = workflow.run_dir / "refinement"
    for label in ("inv1", "c1"):
        write_harness(base_harness(), output / "harnesses" / label)
    (output / "result.json").write_text(json.dumps({
        "selection": {"choice": "c1", "verified": True},
        "runs": {label: {"status": "complete", "verification": {"passed": True}}
                 for label in ("base1", "inv1", "c1")},
        "harness_diffs": {"inv1": {"modified": ["systemprompt.md"]}},
    }), encoding="utf-8")
    workflow.candidate = output / "harnesses" / "c1"
    workflow.evaluated = True
    assert workflow.candidates(console) == ["inv1", "c1"]
    workflow.select_candidate("inv1", console)
    assert workflow.candidate.name == "inv1" and not workflow.evaluated
    assert json.loads((output / "result.json").read_text(encoding="utf-8"))["manual_selection"]["choice"] == "inv1"
    assert "systemprompt.md" in console.file.getvalue()
    try:
        workflow.select_candidate("base1", console)
    except ValueError:
        pass
    else:
        raise AssertionError("baseline was selectable as an improvement")


def test_manual_adoption_does_not_store_auto_selected_experience(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    workspace = tmp_path / "project"
    workspace.mkdir()
    run_dir = tmp_path / "run"
    output = run_dir / "refinement"
    tree = edit_harness(base_harness(), {"system_prompt": "Check boundaries"})
    write_harness(tree, output / "harnesses" / "inv1")
    (output / "result.json").write_text(json.dumps({
        "project": "project-id", "selection": {"choice": "c1", "verified": True},
        "experience": {"harness": tree, "cue": "when fixing code", "lesson": "Check boundaries",
                       "support": "Check boundaries"},
    }), encoding="utf-8")
    workflow = evolution.EvolutionSession(workspace)
    workflow.run_dir, workflow.candidate, workflow.evaluated = run_dir, output / "harnesses" / "inv1", True
    state = SimpleNamespace(config=SimpleNamespace(workspace=workspace), session=SimpleNamespace(id="s"),
                            hooks=Hooks([], []), agent=SimpleNamespace(registry=Registry(workspace)),
                            subagent_services={}, rebuild=lambda: None)
    workflow.adopt(state, Console(file=io.StringIO()))
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["manual_selection"] == {"choice": "inv1", "original": "c1"}
    assert not (home / ".coderill" / "improvements").exists()
    assert evolution.active_marker(workspace).read_text(encoding="utf-8").endswith("inv1")


def test_cli_restart_restores_candidate_but_requires_new_holdout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    workspace = tmp_path / "project"
    workspace.mkdir()
    run_dir = home / ".coderill" / "evolution-runs" / "run1"
    run_dir.mkdir(parents=True)
    (run_dir / "task.json").write_text(json.dumps([{"workspace": str(workspace)}]), encoding="utf-8")
    output = run_dir / "refinement"
    write_harness(base_harness(), output / "harnesses" / "c1")
    (output / "result.json").write_text(json.dumps({"selection": {"choice": "c1"}}), encoding="utf-8")
    workflow = evolution.EvolutionSession(workspace)
    assert workflow.candidate == output / "harnesses" / "c1"
    assert workflow.run_dir == run_dir and not workflow.evaluated


def test_progress_lines_are_readable_in_chinese():
    assert evolution._progress_text("[base1] running task") == "[base1] 正在执行任务"
    assert evolution._progress_text("[base1] status=complete, verification=pass") == (
        "[base1] 任务完成, 验证通过")
    assert evolution._progress_text('{"tasks": 1, "passed": 1, "pass_rate": 1.0}') == "评测完成：1/1 通过"
