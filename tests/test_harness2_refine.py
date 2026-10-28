"""Contrastive task-local harnessing keeps each probe isolated and cited."""

import asyncio
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from coderill import harness2, harness_cli
from coderill.agent.runtime import AgentRuntime
from coderill.config import Config
from coderill.harness2 import _changes, _library, refine
from coderill.permissions import Mode, ReviewManager
from coderill.providers.types import ChatResponse, ChatStreamEvent
from coderill.sandbox import SandboxError, SandboxPolicy
from coderill.session import Session, SessionStore
from coderill.tools import Registry


def test_propose_probe_compose_reruns_selected_overlay():
    runs = []

    async def probe(name, overlay):
        runs.append((name, overlay))
        return {"status": "complete", "trace": f"assistant: {name} inspected the source files",
                "text": name}

    async def editor(stage, evidence):
        if stage == "propose":
            assert evidence["baseline"]["trace"].startswith("assistant: baseline")
            return {"invariant": "check tool results", "domain": "check relevant behavior"}
        if stage == "reflect":
            assert evidence["final"]["text"] == "composed"
            return {"lesson": "When inspecting source, check tool results before editing.",
                    "support": "composed inspected the source files"}
        assert evidence["probes"]["invariant"]["text"] == "invariant"
        return {"choice": "invariant", "observations": [
            "baseline inspected the source files", "invariant inspected the source files"]}

    result = asyncio.run(refine("fix bug", probe, editor, []))
    assert runs == [("baseline", ""), ("invariant", "check tool results"),
                    ("domain", "check relevant behavior"), ("composed", "check tool results")]
    assert result["choice"] == "invariant"
    assert result["experience"]["support"] == "composed inspected the source files"


def test_composer_cannot_claim_evidence_absent_from_trajectory():
    async def probe(name, _overlay):
        return {"status": "complete", "trace": f"assistant: {name} completed the task"}

    async def editor(stage, _evidence):
        return ({"invariant": "one", "domain": "two"} if stage == "propose" else
                {"choice": "invariant", "observations": ["invented baseline event", "invented probe event"]})

    with pytest.raises(ValueError, match="exact trajectory excerpt"):
        asyncio.run(refine("task", probe, editor, []))


def test_unsupported_reflection_is_not_retained():
    async def probe(name, _overlay):
        return {"status": "complete", "trace": f"assistant: {name} completed the task"}

    async def editor(stage, _evidence):
        if stage == "propose":
            return {"invariant": "one", "domain": "two"}
        if stage == "compose":
            return {"choice": "invariant", "observations": [
                "baseline completed the task", "invariant completed the task"]}
        return {"lesson": "This always helps other tasks", "support": "a non-existent final observation"}

    result = asyncio.run(refine("task", probe, editor, []))
    assert result["experience"] is None


def test_library_only_reuses_same_project(tmp_path):
    path = tmp_path / "library.jsonl"
    path.write_text(json.dumps({"project": "A", "overlay": "one"}) + "\n" +
                    json.dumps({"project": "B", "overlay": "two"}) + "\n", encoding="utf-8")
    assert _library(path, "A") == [{"project": "A", "overlay": "one"}]


def test_probe_changes_include_deliverable_without_cache(tmp_path):
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "answer.txt").write_text("wrong", encoding="utf-8")
    (after / "answer.txt").write_text("fixed", encoding="utf-8")
    cache = after / "__pycache__"
    cache.mkdir()
    (cache / "x.pyc").write_bytes(b"binary\0")
    for index in range(31):
        (before / f"same{index:02}.txt").write_text("same", encoding="utf-8")
        (after / f"same{index:02}.txt").write_text("same", encoding="utf-8")
    (before / "zz-deliverable.txt").write_text("old", encoding="utf-8")
    (after / "zz-deliverable.txt").write_text("new", encoding="utf-8")
    changes = _changes(before, after)
    assert "-wrong" in changes and "+fixed" in changes
    assert "+new" in changes
    assert "__pycache__" not in changes


def test_task_overlay_reaches_only_selected_runtime(tmp_path):
    class Provider:
        def __init__(self):
            self.requests = []

        async def stream(self, request):
            self.requests.append(request)
            yield ChatStreamEvent("completed", response=ChatResponse("done"))

    async def run(overlay):
        config = Config(api_key="unused", model="fake", workspace=tmp_path, max_turns=2)
        session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
        provider = Provider()
        runtime = AgentRuntime(config, session, Registry(tmp_path), provider,
                               ReviewManager(tmp_path, mode=Mode.BYPASS))
        runtime.harness_overlay = overlay
        assert (await runtime.run_turn("inspect" )).status == "complete"
        return provider.requests[0].messages[0].content

    assert "special task hint" in asyncio.run(run("special task hint"))
    assert "special task hint" not in asyncio.run(run(""))


def test_cli_keeps_source_untouched_and_records_supported_experience(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "answer.txt").write_text("original", encoding="utf-8")
    output, library = tmp_path / "output", tmp_path / "library.jsonl"

    class Editor:
        async def complete(self, request):
            instruction = request.messages[0].content
            if "Edit the current harness" in instruction:
                evidence = json.loads(request.messages[1].content)
                answer = {"patch": {"system_prompt": "verify the result" if evidence["dimension"] == "inv"
                                     else "inspect inputs"}}
            elif "compose a selective" in instruction:
                answer = {"patch": {"system_prompt": "verify the result"}, "citations": {
                    "base1": "base1 checked the file", "inv1": "inv1 checked the file"}}
            else:
                answer = {"cue": "When changing files", "lesson": "When changing files, verify the final result.",
                          "support": "c1 checked the file"}
            return ChatResponse(json.dumps(answer))

    class Runtime:
        def __init__(self, config, session, *_args, **_kwargs):
            self.workspace, self.session = config.workspace, session
            self.harness_overlay = ""

        async def run_turn(self, _task):
            stage = self.workspace.name
            self.session.record({"role": "assistant", "content": f"{stage} checked the file"})
            (self.workspace / "answer.txt").write_text(self.harness_overlay or "baseline", encoding="utf-8")
            return SimpleNamespace(status="complete", text="done", error="")

    monkeypatch.setattr(Config, "from_env", classmethod(
        lambda cls, workspace, **_kwargs: cls(api_key="unused", model="fake", workspace=workspace)))
    monkeypatch.setattr(harness_cli, "create_provider", lambda _config: Editor())
    monkeypatch.setattr(harness_cli, "AgentRuntime", Runtime)
    monkeypatch.setattr(sys, "argv", ["coderill-refine", str(source), "fix it", "--output", str(output),
                                    "--library", str(library)])
    harness2.main()
    assert (source / "answer.txt").read_text(encoding="utf-8") == "original"
    assert (output / "c1" / "answer.txt").read_text(encoding="utf-8").endswith("verify the result")
    assert (output / "harnesses" / "c1" / "systemprompt.md").read_text() == "verify the result"
    assert json.loads(library.read_text(encoding="utf-8"))["lesson"] == "When changing files, verify the final result."
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    entry = result["library_entry"]
    assert (library.with_suffix("") / result["project"] / "general" / "INDEX.md").is_file()
    assert (Path(entry) / "harness" / "systemprompt.md").read_text() == "verify the result"


@pytest.mark.parametrize("accept_candidate", [True, False])
@pytest.mark.parametrize("defer_library", [True, False])
def test_eval_manifest_verifies_each_probe_before_adoption(tmp_path, monkeypatch, accept_candidate, defer_library):
    source = tmp_path / "source"
    source.mkdir()
    (source / "answer.txt").write_text("original", encoding="utf-8")
    (tmp_path / "verify.py").write_text(
        "from pathlib import Path\n"
        "Path('verifier-marker.txt').write_text('temporary')\n"
        f"assert Path('answer.txt').read_text() == {'pass' if accept_candidate else 'never'!r}\n",
        encoding="utf-8")
    manifest = tmp_path / "tasks.json"
    manifest.write_text(json.dumps([{"id": "example", "workspace": "source", "prompt": "fix it",
                                    "verify": [["{python}", "{manifest_dir}/verify.py"]]}]), encoding="utf-8")
    output, library = tmp_path / "output", tmp_path / "library"
    before = tmp_path / "before"
    shutil.copytree(source, before)

    class Editor:
        async def complete(self, request):
            instruction = request.messages[0].content
            if "Edit the current harness" in instruction:
                evidence = json.loads(request.messages[1].content)
                answer = {"patch": {"system_prompt": "verified strategy" if evidence["dimension"] == "inv"
                                     else "other strategy"}}
            elif "compose a selective" in instruction:
                answer = {"patch": {"system_prompt": "unverified strategy"}, "citations": {
                    "base1": "base1 checked the file", "inv1": "inv1 checked the file"}}
            else:
                answer = {"cue": "When fixing a task", "lesson": "Keep the strategy that passes the verifier.",
                          "support": "inv1 checked the file"}
            return ChatResponse(json.dumps(answer))

    class Runtime:
        def __init__(self, config, session, *_args, **_kwargs):
            self.workspace, self.session = config.workspace, session
            self.harness_overlay = ""
            assert config.sandbox == "off"
            assert not {"bash", "diagnostics", "apply_patch"} & set(_args[0].names())

        async def run_turn(self, _task):
            self.session.record({"role": "assistant", "content": f"{self.workspace.name} checked the file"})
            (self.workspace / "answer.txt").write_text(
                "pass" if "verified strategy" in self.harness_overlay and
                "unverified strategy" not in self.harness_overlay else "fail", encoding="utf-8")
            return SimpleNamespace(status="complete", text="done", error="")

    monkeypatch.setattr(Config, "from_env", classmethod(
        lambda cls, workspace, **kwargs: cls(api_key="unused", model="fake", workspace=workspace,
                                             sandbox=kwargs["overrides"]["sandbox"])))
    monkeypatch.setattr(harness_cli, "create_provider", lambda _config: Editor())
    monkeypatch.setattr(harness_cli, "AgentRuntime", Runtime)
    monkeypatch.setattr(sys, "argv", ["coderill-refine", "--eval-manifest", str(manifest), "--case-id", "example",
                                    "--source-snapshot", str(before), "--output", str(output),
                                    "--library", str(library), "--no-docker",
                                    *(["--defer-library"] if defer_library else [])])
    if accept_candidate:
        harness2.main()
    else:
        with pytest.raises(SystemExit) as error:
            harness2.main()
        assert error.value.code == 1
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["evaluation"]["case_id"] == "example"
    assert result["evaluation"]["project_source"] == str(source)
    assert result["source"] == str(before)
    assert len(result["runs"]) == 4
    assert all("verification" in run for run in result["runs"].values())
    assert result["selection"]["verified"] is accept_candidate
    assert result["selection"]["choice"] == ("inv1" if accept_candidate else "c1")
    assert (output / "inv1" / "answer.txt").read_text(encoding="utf-8") == "pass"
    assert not (output / "inv1" / "verifier-marker.txt").exists()
    assert (source / "answer.txt").read_text(encoding="utf-8") == "original"
    assert (library / result["project"] / "general" / "entries.jsonl").exists() is (
        accept_candidate and not defer_library)


def test_verifier_uses_docker_boundary_and_read_only_manifest_mount(tmp_path, monkeypatch):
    seen = {}

    class Job:
        def __init__(self, policy, *, verifier_dir):
            seen["policy"], seen["verifier_dir"] = policy, verifier_dir

        def run(self, argv, *, timeout):
            seen["argv"], seen["timeout"] = argv, timeout
            return SimpleNamespace(returncode=0, stdout=b"passed", stderr=b"")

    monkeypatch.setattr(harness_cli, "DockerJob", Job)
    policy = SandboxPolicy(tmp_path, backend="docker", network="none")
    result = harness_cli._verify([["{python}", "{manifest_dir}/verify.py", "case"]], tmp_path, tmp_path, policy)
    assert result["passed"]
    assert seen["policy"].enabled and seen["policy"].workspace != tmp_path
    assert seen["verifier_dir"] == tmp_path
    assert seen["argv"] == ["python", "/verifier/verify.py", "case"]
    assert seen["timeout"] == 120


def test_refinement_checks_docker_before_model_calls(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "answer.txt").write_text("before", encoding="utf-8")
    output = tmp_path / "output"
    monkeypatch.setattr(Config, "from_env", classmethod(
        lambda cls, workspace, **_kwargs: cls(api_key="unused", workspace=workspace,
                                               sandbox="docker", sandbox_network="none")))
    def missing_image(*_args):
        raise SandboxError("missing image")

    monkeypatch.setattr(harness_cli, "docker", missing_image)
    monkeypatch.setattr(harness_cli, "create_provider", lambda _config: pytest.fail("model called before preflight"))
    monkeypatch.setattr(sys, "argv", ["coderill-refine", str(source), "fix it", "--output", str(output)])
    with pytest.raises(SandboxError, match="missing image"):
        harness2.main()
    assert "missing image" in (output / "error.txt").read_text(encoding="utf-8")
