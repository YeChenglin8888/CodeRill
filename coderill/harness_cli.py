"""Isolated command-line runner for complete task-level Harness² refinement."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
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
from .permissions import Mode, ReviewManager
from .providers import create_provider
from .session import Session, SessionStore
from .tools import Registry
from .tools.advanced import ApplyPatchTool, DiagnosticsTool, ReadMultiTool
from .tools.agent import HarnessDelegateTool
from .tools.decision import JevDecisionTool
from .tools.files import EditFileTool, ReadFileTool, WriteFileTool
from .tools.glob_tool import GlobTool
from .tools.grep import GrepTool
from .tools.shell import BashTool
from .tools.skills import LoadSkillTool

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Refine CodeRill harnesses in isolated Docker workspaces.")
    parser.add_argument("workspace", type=Path)
    parser.add_argument("task")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--library", type=Path, help="improvement harness directory or legacy JSONL path")
    parser.add_argument("--domain", default="general", help="experience domain within this project")
    parser.add_argument("--mode", choices=("seq", "agg"), default="seq")
    parser.add_argument("--k", type=int, choices=range(1, 9), metavar="1..8", default=1)
    parser.add_argument("--decision-model", choices=("editor", "jev"), default="editor")
    args = parser.parse_args()
    source, output = args.workspace.resolve(), args.output.resolve()
    library_path = args.library.resolve() if args.library else None
    if (not source.is_dir() or output.is_relative_to(source) or output.exists() or
            (library_path and library_path.is_relative_to(source))):
        parser.error("workspace must exist; output and library must be outside it; output must be new")
    project = hashlib.sha256(str(source).encode()).hexdigest()[:16]
    library = ImprovementLibrary(library_path, project, args.domain) if library_path else None
    prior = library.read() if library else []
    output.mkdir(parents=True)
    try:
        with tempfile.TemporaryDirectory(prefix="coderill-harness2-") as temporary:
            snapshot = Path(temporary) / "snapshot"
            # shortcut: copied probes omit Git history and local secrets; use Git snapshots for history-dependent tasks.
            shutil.copytree(source, snapshot, symlinks=True, ignore=shutil.ignore_patterns(
                ".git", ".venv", ".coderill", "node_modules", "__pycache__", ".env", ".env.*",
                ".harness2"))
            config = Config.from_env(snapshot, overrides={"sandbox": "docker", "sandbox_network": "none"})
            if args.decision_model == "jev" and (args.k > 1 or prior) and not config.typesafe_api_key:
                raise ValueError("--decision-model jev requires TYPESAFE_API_KEY")
            editor_provider = create_provider(config)
            editor_usage: list[dict] = []

            async def probe(label: str, tree: dict) -> dict:
                harness_dir = write_harness(tree, output / "harnesses" / label)
                workspace = output / label
                shutil.copytree(snapshot, workspace, symlinks=True)
                probe_config = Config.from_env(workspace, overrides={"sandbox": "docker", "sandbox_network": "none"})
                probe_config = replace(probe_config, max_turns=min(probe_config.max_turns, 12),
                    max_tool_rounds=min(probe_config.max_tool_rounds, 40),
                    max_turn_seconds=min(probe_config.max_turn_seconds, 180))
                overlay, skills, hooks = bind_harness(tree, workspace)
                session = Session.new(probe_config.model, str(workspace), SessionStore(output / "sessions"))
                policy = probe_config.sandbox_policy
                registry = Registry(workspace, policy)
                for tool in (ReadFileTool(workspace), WriteFileTool(workspace), EditFileTool(workspace),
                             ReadMultiTool(workspace), GlobTool(workspace), GrepTool(workspace),
                             BashTool(workspace, policy), DiagnosticsTool(workspace, policy),
                             ApplyPatchTool(workspace, policy)):
                    registry.register(tool)
                if skills:
                    registry.register(LoadSkillTool(skills))
                if tree["subagents"]:
                    registry.register(HarnessDelegateTool(SubagentService(
                        probe_config, session, hooks=hooks, skills=skills,
                        harness_overlay=overlay, harness_roles=tree["subagents"]), tree["subagents"]))
                if probe_config.jev_decisions_enabled:
                    registry.register(JevDecisionTool(probe_config.typesafe_api_key,
                                                     probe_config.typesafe_model))
                runtime = AgentRuntime(probe_config, session, registry, create_provider(probe_config),
                                       ReviewManager(workspace, mode=Mode.BYPASS, policy=policy),
                                       hooks=hooks, skills=skills)
                runtime.harness_overlay = overlay
                result = await runtime.run_turn(args.task)
                return {"status": result.status, "text": result.text or result.error,
                        "trace": _trace(session), "changes": _changes(snapshot, workspace),
                        "usage": session.usage, "workspace": str(workspace), "session": session.id,
                        "harness_dir": str(harness_dir)}

            async def editor(stage: str, evidence: dict) -> dict:
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

            decision = asyncio.run(run_harness(args.task, probe, editor, prior,
                mode=args.mode, k=args.k, selector=selector if args.decision_model == "jev" else None,
                reuse_selector=reuse_selector if args.decision_model == "jev" else None))
            decision.update({"model": config.model, "provider": config.provider,
                             "project": project, "domain": args.domain,
                             "editor_usage": editor_usage})
            result_path = output / "result.json"
            result_path.write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
            if library and decision["experience"]:
                entry = library.add(decision["experience"], selection=decision["selection"], result=result_path)
                decision["library_entry"] = str(entry)
                result_path.write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"selection": decision["selection"], "workspace": decision["workspace"],
                              "output": str(output)}, ensure_ascii=False))
    except Exception as exc:
        (output / "error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        raise
