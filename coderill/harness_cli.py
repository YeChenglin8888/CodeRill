"""Isolated command-line runner for complete task-level Harness² refinement."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from .agent.runtime import AgentRuntime
from .agent.subagents import SubagentService
from .config import Config
from .harness2 import _ask, _changes, _trace
from .harness_engine import run_harness
from .harness_library import ImprovementLibrary
from .harness_tree import bind_harness, write_harness
from .jev import choose
from .memory import MemoryStore
from .permissions import Mode, ReviewManager
from .providers import create_provider
from .sandbox import GATEWAY_IMAGE, DockerJob, SandboxError, SandboxPolicy, docker
from .session import Session, SessionStore
from .tools import Registry
from .tools.advanced import ApplyPatchTool, DiagnosticsTool, ReadMultiTool
from .tools.agent import HarnessDelegateTool
from .tools.decision import JevDecisionTool
from .tools.files import EditFileTool, ReadFileTool, WriteFileTool
from .tools.glob_tool import GlobTool
from .tools.grep import GrepTool
from .tools.memory import MemoryReadTool, MemorySearchTool
from .tools.shell import BashTool
from .tools.skills import LoadSkillTool, SkillSearchTool

_INSTRUCTIONS = {
    "reuse": ("Select useful prior experiences by zero-based index and distinct component names. "
              "Return {\"entries\": [{\"index\": number, \"components\": [...]}]}. "
              "An empty entries list means no reuse. "
              "When selected_index is supplied, return {\"index\": selected_index, "
              "\"components\": [...]} using only that entry. "
              "Reuse only evidence relevant to this task. Never copy task answers."),
    "propose": ("Edit the current harness for the task, based on observed baseline behavior. "
                "Return {\"patch\": {...}} containing only changed components. "
                "For dimension inv, focus on general workflow; for dom, focus on task-specific method. "
                "Components: system_prompt, guardrails, memory, tool_guidance are text; "
                "skills, scripts, subagents are name-to-text maps (scripts are Python); "
                "middleware is a JSON plugin with blocked_tools, pre_rules "
                "[{tool, argument_contains, message}], and post_rules "
                "[{tool, result_contains, message}]. Pre rules block matching calls; "
                "post rules append a hint to matching tool results. "
                "Keep edits small. Do not include task answers or secrets."),
    "compose": ("Compare current and candidate runs, then compose a selective improvement as a harness patch. "
                "Return {\"patch\": {...}, \"citations\": {label: exact excerpt}}. "
                "Cite at least one 12+ character excerpt from the current run and one candidate, "
                "each appearing verbatim in that run's text, trace or changes. "
                "Use an empty patch only when no supported improvement exists."),
    "select": ("Choose the best completed final deliverable based only on the anonymous artifacts. "
               "Return {\"choice\": \"A\"} using one listed candidate ID."),
    "reflect": ("Distill one transferable lesson from the chosen run. Return "
                "{\"cue\": \"when it applies\", \"lesson\": \"what to do\", "
                "\"support\": \"exact 12+ character excerpt from final text, trace or changes\", "
                "\"edits\": \"what the adopted component edits did\"}. "
                "Return empty strings when evidence does not support a lesson. No secrets or task answers."),
}


def _reuse_options(task: str, experiences: list[dict]) -> dict[str, str]:
    ranked = sorted(enumerate(experiences), key=lambda item: (
        sum(word in (item[1].get("cue", "") + " " + item[1].get("lesson", "")).lower()
            for word in set(task.lower().split()) if len(word) > 1), item[0]), reverse=True)
    return {"none": "No past experience is relevant to the current task", **{
        f"E{i}": (entry.get("cue", "") + ": " + entry.get("lesson", ""))[:2000]
        for i, entry in ranked[:7]}}


def _verify(commands: list[list[str]], manifest_dir: Path, workspace: Path, policy: SandboxPolicy) -> dict:
    checks = []
    with tempfile.TemporaryDirectory(prefix="coderill-verify-", ignore_cleanup_errors=True) as temporary:
        verify_workspace = Path(temporary) / "workspace"
        shutil.copytree(workspace, verify_workspace, symlinks=True)
        verify_policy = replace(policy, workspace=verify_workspace)
        for command in commands:
            argv = [part.replace("{python}", "python" if policy.enabled else sys.executable)
                    .replace("{manifest_dir}", "/verifier" if policy.enabled else str(manifest_dir))
                    for part in command]
            try:
                if policy.enabled:
                    result = DockerJob(verify_policy, verifier_dir=manifest_dir).run(argv, timeout=120)
                    output = (result.stdout + result.stderr).decode(errors="replace")
                else:
                    result = subprocess.run(argv, cwd=verify_workspace, capture_output=True, text=True,
                                            timeout=120, check=False)
                    output = result.stdout + result.stderr
                checks.append({"argv": argv, "exit_code": result.returncode,
                               "output_tail": output[-2000:]})
            except (OSError, subprocess.TimeoutExpired, SandboxError) as exc:
                checks.append({"argv": argv, "exit_code": None, "error": str(exc)})
                break
    return {"passed": len(checks) == len(commands) and all(check["exit_code"] == 0 for check in checks),
            "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description="Refine CodeRill harnesses in isolated Docker workspaces.")
    parser.add_argument("workspace", type=Path, nargs="?")
    parser.add_argument("task", nargs="?")
    parser.add_argument("--eval-manifest", type=Path, help="existing evaluation manifest with verifier commands")
    parser.add_argument("--case-id", help="task ID within --eval-manifest")
    parser.add_argument("--source-snapshot", type=Path, help="pre-task workspace copy for an evaluation case")
    parser.add_argument("--feedback", help="user feedback to include in the refinement task")
    parser.add_argument("--defer-library", action="store_true", help="read prior experience but save only after holdout evaluation")
    parser.add_argument("--no-docker", action="store_true", help="run file-only probes and trusted verifiers on this host")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--library", type=Path, help="improvement harness directory or legacy JSONL path")
    parser.add_argument("--domain", default="general", help="experience domain within this project")
    parser.add_argument("--mode", choices=("seq", "agg"), default="seq")
    parser.add_argument("--k", type=int, choices=range(1, 9), metavar="1..8", default=1)
    parser.add_argument("--decision-model", choices=("editor", "jev"), default="editor")
    args = parser.parse_args()
    if bool(args.eval_manifest) != bool(args.case_id):
        parser.error("--eval-manifest and --case-id must be supplied together")
    if args.source_snapshot and not args.eval_manifest:
        parser.error("--source-snapshot requires --eval-manifest")
    if args.no_docker and not args.eval_manifest:
        parser.error("--no-docker requires --eval-manifest for external verification")
    manifest_info = None
    commands = None
    if args.eval_manifest:
        if args.workspace or args.task:
            parser.error("workspace and task are supplied by the evaluation case")
        manifest_path = args.eval_manifest.resolve()
        manifest_bytes = manifest_path.read_bytes()
        cases = json.loads(manifest_bytes)
        if not isinstance(cases, list):
            parser.error("evaluation manifest must contain a list of cases")
        matches = [case for case in cases if isinstance(case, dict) and case.get("id") == args.case_id]
        if len(matches) != 1:
            parser.error("--case-id must match exactly one evaluation case")
        case = matches[0]
        commands = case.get("verify")
        if (not isinstance(case.get("workspace"), str) or not isinstance(case.get("prompt"), str)
                or not isinstance(commands, list) or not commands or any(
                    not isinstance(command, list) or not command or any(
                        not isinstance(part, str) or not part for part in command) for command in commands)):
            parser.error("evaluation case needs workspace, prompt and non-empty verifier commands")
        project_source = (manifest_path.parent / case["workspace"]).resolve()
        source = args.source_snapshot.resolve() if args.source_snapshot else project_source
        task = case["prompt"]
        manifest_info = {"path": str(manifest_path), "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                         "case_id": args.case_id, "project_source": str(project_source)}
    else:
        if not args.workspace or not args.task:
            parser.error("workspace and task are required without --eval-manifest")
        source, task = args.workspace.resolve(), args.task
        project_source = source
    if args.feedback:
        task += "\n\nUser feedback: " + args.feedback
    output = args.output.resolve()
    library_path = args.library.resolve() if args.library else None
    if (not source.is_dir() or output.is_relative_to(source) or output.exists() or
            (library_path and library_path.is_relative_to(source))):
        parser.error("workspace must exist; output and library must be outside it; output must be new")
    project = hashlib.sha256(str(project_source).encode()).hexdigest()[:16]
    library = ImprovementLibrary(library_path, project, args.domain) if library_path else None
    prior = library.read() if library else []
    memory = MemoryStore(project_source)
    output.mkdir(parents=True)
    try:
        with tempfile.TemporaryDirectory(prefix="coderill-harness2-") as temporary:
            snapshot = Path(temporary) / "snapshot"
            # shortcut: copied probes omit Git history and local secrets; use Git snapshots for history-dependent tasks.
            shutil.copytree(source, snapshot, symlinks=True, ignore=shutil.ignore_patterns(
                ".git", ".venv", ".coderill", "node_modules", "__pycache__", ".env", ".env.*",
                ".harness2"))
            sandbox = "off" if args.no_docker else "docker"
            config = Config.from_env(snapshot, overrides={"sandbox": sandbox, "sandbox_network": "none"})
            if config.sandbox_policy.enabled:
                docker("image", "inspect", config.sandbox_policy.image)
                docker("image", "inspect", GATEWAY_IMAGE)
            if args.decision_model == "jev" and (args.k > 1 or prior) and not config.typesafe_api_key:
                raise ValueError("--decision-model jev requires TYPESAFE_API_KEY")
            editor_provider = create_provider(config)
            editor_usage: list[dict] = []

            async def probe(label: str, tree: dict) -> dict:
                print(f"[{label}] running task", flush=True)
                harness_dir = write_harness(tree, output / "harnesses" / label)
                workspace = output / label
                shutil.copytree(snapshot, workspace, symlinks=True)
                probe_config = Config.from_env(workspace, overrides={"sandbox": sandbox, "sandbox_network": "none"})
                probe_config = replace(probe_config, max_turns=min(probe_config.max_turns, 12),
                    max_tool_rounds=min(probe_config.max_tool_rounds, 40),
                    max_turn_seconds=min(probe_config.max_turn_seconds, 180))
                overlay, skills, hooks = bind_harness(tree, workspace)
                session = Session.new(probe_config.model, str(workspace), SessionStore(output / "sessions"))
                policy = probe_config.sandbox_policy
                registry = Registry(workspace, policy)
                tools = [ReadFileTool(workspace), WriteFileTool(workspace), EditFileTool(workspace),
                         ReadMultiTool(workspace), GlobTool(workspace), GrepTool(workspace)]
                if not args.no_docker:
                    tools += [BashTool(workspace, policy), DiagnosticsTool(workspace, policy),
                              ApplyPatchTool(workspace, policy)]
                for tool in tools:
                    registry.register(tool)
                registry.register(MemorySearchTool(memory))
                registry.register(MemoryReadTool(memory))
                if skills:
                    registry.register(LoadSkillTool(skills))
                    if probe_config.routing.get("enabled", True):
                        registry.register(SkillSearchTool(skills, probe_config.routing.get("embedding")))
                if tree["subagents"]:
                    registry.register(HarnessDelegateTool(SubagentService(
                        probe_config, session, hooks=hooks, skills=skills,
                        harness_overlay=overlay, harness_roles=tree["subagents"]), tree["subagents"]))
                if probe_config.jev_decisions_enabled:
                    registry.register(JevDecisionTool(probe_config.typesafe_api_key,
                                                     probe_config.typesafe_model))
                runtime = AgentRuntime(probe_config, session, registry, create_provider(probe_config),
                                       ReviewManager(workspace, mode=Mode.BYPASS, policy=policy),
                                       hooks=hooks, skills=skills, memory=memory, memory_write=False)
                runtime.harness_overlay = overlay
                result = await runtime.run_turn(task)
                run = {"status": result.status, "text": result.text or result.error,
                        "trace": _trace(session), "changes": _changes(snapshot, workspace),
                        "usage": session.usage, "workspace": str(workspace), "session": session.id,
                        "harness_dir": str(harness_dir),
                        "settings": {name: getattr(probe_config, name) for name in (
                            "model", "provider", "max_turns", "max_tool_rounds", "max_turn_seconds",
                            "sandbox", "sandbox_network")}}
                if commands is not None:
                    run["verification"] = (await asyncio.to_thread(
                        _verify, commands, manifest_path.parent, workspace, policy)
                                           if result.status == "complete" else
                                           {"passed": False, "checks": [], "reason": f"agent status: {result.status}"})
                verdict = ("pass" if run["verification"]["passed"] else "fail") if commands else "unverified"
                print(f"[{label}] status={result.status}, verification={verdict}", flush=True)
                return run

            async def editor(stage: str, evidence: dict) -> dict:
                print(f"[editor] {stage}", flush=True)
                return await _ask(editor_provider, _INSTRUCTIONS[stage], evidence,
                                  usage_log=editor_usage, stage=stage)

            async def selector(task: str, candidates: dict) -> dict:
                options = {name: (item["text"] + "\n" + item["changes"])[:2000]
                           for name, item in candidates.items()}
                return await asyncio.to_thread(choose, {"task": task},
                    "Which candidate best satisfies the task based on its deliverable?", options,
                    key=config.typesafe_api_key, model=config.typesafe_model)

            async def reuse_selector(task: str, experiences: list[dict]) -> dict:
                options = _reuse_options(task, experiences)
                decision = await asyncio.to_thread(choose, {"task": task},
                    "Which prior experience is most relevant to this task, if any?", options,
                    key=config.typesafe_api_key, model=config.typesafe_model)
                return {"index": None if decision["choice"] == "none" else
                        int(decision["choice"][1:]), "decision": decision}

            decision = asyncio.run(run_harness(task, probe, editor, prior,
                mode=args.mode, k=args.k, selector=selector if args.decision_model == "jev" else None,
                reuse_selector=reuse_selector if args.decision_model == "jev" else None))
            decision.update({"task": task, "source": str(source),
                             "model": config.model, "provider": config.provider,
                             "project": project, "domain": args.domain,
                             "editor_usage": editor_usage})
            if manifest_info:
                decision["evaluation"] = manifest_info
            result_path = output / "result.json"
            result_path.write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
            if library and decision["experience"] and not args.defer_library:
                entry = library.add(decision["experience"], selection=decision["selection"], result=result_path)
                decision["library_entry"] = str(entry)
                experience = decision["experience"]
                memory.add(key=experience["cue"], kind="experience", content=experience["lesson"],
                           evidence=experience["support"], source=f"harness:{entry}", source_type="harness")
                result_path.write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"selection": decision["selection"], "workspace": decision["workspace"],
                              "output": str(output)}, ensure_ascii=False))
            if commands is not None and not decision["selection"]["verified"]:
                raise SystemExit(1)
    except Exception as exc:
        (output / "error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        raise
