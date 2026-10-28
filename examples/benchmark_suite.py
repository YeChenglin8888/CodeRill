"""Opt-in, isolated multi-task evaluation; one model attempt per task."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from rich.console import Console

from coderill.app.factory import build_state
from coderill.config import Config
from coderill.harness_tree import bind_harness, read_harness
from coderill.memory import MemoryStore
from coderill.session import Session, SessionStore
from coderill.tools import Registry
from coderill.tools.advanced import ReadMultiTool
from coderill.tools.agent import HarnessDelegateTool
from coderill.tools.files import EditFileTool, ReadFileTool, WriteFileTool
from coderill.tools.glob_tool import GlobTool
from coderill.tools.grep import GrepTool
from coderill.tools.memory import MemoryReadTool, MemorySearchTool
from coderill.tools.skills import LoadSkillTool, SkillSearchTool


def load_manifest(path: Path) -> list[dict]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or not manifest or not all(
        isinstance(case, dict) and all(isinstance(case.get(key), str) and case[key].strip()
                                      for key in ("id", "workspace", "prompt"))
        for case in manifest
    ):
        raise ValueError("manifest must be a non-empty list of tasks with id, workspace, and prompt")
    if len({case["id"] for case in manifest}) != len(manifest):
        raise ValueError("manifest task ids must be unique")
    return manifest


def verifier_command(argv: list[str], manifest_dir: Path) -> list[str]:
    return [part.replace("{python}", sys.executable).replace("{manifest_dir}", str(manifest_dir.resolve()))
            for part in argv]


def summarize(results: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for result in results:
        counts[result["outcome"]] = counts.get(result["outcome"], 0) + 1
    return {"tasks": len(results), "passed": counts.get("passed", 0),
            "pass_rate": counts.get("passed", 0) / len(results) if results else 0.0,
            "all_passed": bool(results) and counts.get("passed", 0) == len(results),
            "outcomes": counts}


def run_case(case: dict, manifest_dir: Path, *, provider: str | None = None,
             harness: dict | None = None, no_docker: bool = False, memory_mode: str | None = None,
             routing_enabled: bool | None = None) -> dict:
    verifiers = case.get("verify", [])
    if not isinstance(verifiers, list) or any(
        not isinstance(argv, list) or not argv or not all(isinstance(part, str) and part for part in argv)
        for argv in verifiers
    ):
        raise ValueError("verify commands must be non-empty argument arrays")
    source = (manifest_dir / case["workspace"]).resolve()
    if not source.is_dir():
        raise ValueError(f"task workspace is missing: {source}")
    started = time.monotonic()
    # shortcut: Windows device names made by a candidate may survive cleanup; add a janitor if this recurs.
    with tempfile.TemporaryDirectory(prefix="coderill-eval-", ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        workspace = root / "workspace"
        shutil.copytree(source, workspace, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
        config = Config.from_env(workspace, overrides={"provider": provider,
                                                       "sandbox": "off" if no_docker else None})
        if routing_enabled is not None:
            config = replace(config, routing={**config.routing, "enabled": routing_enabled})
        session = Session.new(config.model, str(workspace), SessionStore(root / "sessions"))
        state = build_state(config, session, Console(quiet=True), auto_approve=True, plan=False)
        state.agent.on_event = lambda _event: None
        try:
            if no_docker:
                state.agent.registry = Registry(workspace, config.sandbox_policy)
                for tool in (ReadFileTool(workspace), WriteFileTool(workspace), EditFileTool(workspace),
                             ReadMultiTool(workspace), GlobTool(workspace), GrepTool(workspace)):
                    state.agent.registry.register(tool)
            if harness is not None:
                overlay, skills, hooks = bind_harness(harness, workspace)
                state.agent.harness_overlay = overlay
                state.agent.skills = skills
                state.agent.hooks = hooks
                if skills:
                    state.agent.registry.register(LoadSkillTool(skills))
                    if config.routing.get("enabled", True):
                        state.agent.registry.register(SkillSearchTool(skills, config.routing.get("embedding")))
                if harness["subagents"]:
                    service = state.subagent_services[session.id]
                    service.hooks, service.skills = hooks, skills
                    service.harness_overlay, service.harness_roles = overlay, harness["subagents"]
                    state.agent.registry.register(HarnessDelegateTool(service, harness["subagents"]))
            if memory_mode is not None:
                state.agent.memory = None
                state.agent.memory_write = False
                for name in ("memory_search", "memory_read"):
                    state.agent.registry._tools.pop(name, None)
                if memory_mode != "off":
                    memory = MemoryStore(workspace, root / "memories")
                    if memory_mode == "lexical":
                        memory._model_checked = True
                    for index, record in enumerate(case.get("memory_history", [])):
                        memory.add(key=record["key"], kind=record.get("kind", "experience"),
                                   content=record["content"], evidence=record["content"],
                                   source=f"fixture:{index}", source_type="user",
                                   update=memory_mode == "lifecycle" and record.get("update", False))
                    state.agent.memory = memory
                    state.agent.registry.register(MemorySearchTool(memory))
                    state.agent.registry.register(MemoryReadTool(memory))
            turn = state.run_async(state.agent.run_turn(case["prompt"]))
        finally:
            state.close()
            for manager in state.service_processes.values():
                for process_id in list(manager.running):
                    manager.stop(process_id)
        checks = []
        if turn.status == "complete":
            for command in verifiers:
                argv = verifier_command(command, manifest_dir)
                completed = subprocess.run(argv, cwd=workspace, capture_output=True, text=True,
                                           timeout=120, check=False)
                checks.append({"argv": argv, "exit_code": completed.returncode,
                               "output_tail": (completed.stdout + completed.stderr)[-2000:]})
        outcome = ("agent_failed" if turn.status != "complete" else
                   "unverified" if not checks else
                   "verification_failed" if any(check["exit_code"] for check in checks) else "passed")
        return {"id": case["id"], "outcome": outcome, "turn_status": turn.status,
                "error": turn.error, "checks": checks, "usage": session.usage,
                "model": config.model, "provider": config.provider,
                "routing_enabled": config.routing.get("enabled", True),
                "settings": {key: getattr(config, key) for key in (
                    "temperature", "max_tokens", "max_turns", "max_tool_rounds", "max_turn_seconds",
                    "max_retries", "request_timeout", "max_context_tokens", "benchmark", "sandbox",
                    "sandbox_profile", "sandbox_network", "thinking_enabled")},
                "approval_policy": "bypass",
                "case_metadata": {key: value for key, value in case.items() if key != "verify"},
                "elapsed_seconds": round(time.monotonic() - started, 3)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a fixed CodeRill task manifest with isolated workspaces.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", choices=("openai", "anthropic"))
    parser.add_argument("--harness", type=Path, help="adopted harness directory to evaluate on every case")
    parser.add_argument("--no-docker", action="store_true", help="file-only agent; trusted verifiers run on this host")
    parser.add_argument("--case-id", help="run one case from the manifest")
    parser.add_argument("--routing", choices=("on", "off"), help="force routing on or off for a paired run")
    parser.add_argument("--memory-mode", choices=("off", "lexical", "hybrid", "lifecycle"),
                        default="off", help="evaluate seeded multi-session memory under one condition")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    if args.case_id:
        manifest = [case for case in manifest if case["id"] == args.case_id]
        if len(manifest) != 1:
            parser.error("--case-id must match exactly one evaluation case")
    harness = read_harness(args.harness.resolve()) if args.harness else None
    results = []
    for case in manifest:
        print(f"[{case['id']}] running", flush=True)
        try:
            results.append(run_case(case, args.manifest.parent, provider=args.provider, harness=harness,
                                    no_docker=args.no_docker, memory_mode=args.memory_mode,
                                    routing_enabled=None if args.routing is None else args.routing == "on"))
        except Exception as exc:  # noqa: BLE001 - one broken task must not erase other results
            results.append({"id": case["id"], "outcome": "infrastructure_error", "error": str(exc)})
        print(f"[{case['id']}] {results[-1]['outcome']}", flush=True)
    report = {"manifest": str(args.manifest),
              "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
              "summary": summarize(results), "results": results}
    if args.memory_mode is not None:
        report["memory_mode"] = args.memory_mode
    if harness is not None:
        report["harness"] = {"path": str(args.harness.resolve()),
                             "sha256": hashlib.sha256(json.dumps(harness, sort_keys=True).encode()).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    if not report["summary"]["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
