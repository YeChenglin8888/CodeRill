"""Opt-in, isolated multi-task evaluation; one model attempt per task."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from rich.console import Console

from coderill.app.factory import build_state
from coderill.config import Config
from coderill.session import Session, SessionStore


def summarize(results: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for result in results:
        counts[result["outcome"]] = counts.get(result["outcome"], 0) + 1
    return {"tasks": len(results), "passed": counts.get("passed", 0),
            "pass_rate": counts.get("passed", 0) / len(results) if results else 0.0,
            "outcomes": counts}


def run_case(case: dict, manifest_dir: Path, *, provider: str | None = None) -> dict:
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
    with tempfile.TemporaryDirectory(prefix="coderill-eval-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        shutil.copytree(source, workspace, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
        config = Config.from_env(workspace, overrides={"provider": provider})
        session = Session.new(config.model, str(workspace), SessionStore(root / "sessions"))
        state = build_state(config, session, Console(quiet=True), auto_approve=True, plan=False)
        state.agent.on_event = lambda _event: None
        try:
            turn = state.run_async(state.agent.run_turn(case["prompt"]))
        finally:
            state.close()
            for manager in state.service_processes.values():
                for process_id in list(manager.running):
                    manager.stop(process_id)
        checks = []
        if turn.status == "complete":
            for argv in verifiers:
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
                "elapsed_seconds": round(time.monotonic() - started, 3)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a fixed CodeRill task manifest with isolated workspaces.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", choices=("openai", "anthropic"))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or not all(isinstance(case, dict) and
                                                  {"id", "workspace", "prompt"} <= case.keys()
                                                  for case in manifest):
        raise ValueError("manifest must be a list of tasks with id, workspace, and prompt")
    results = []
    for case in manifest:
        try:
            results.append(run_case(case, args.manifest.parent, provider=args.provider))
        except Exception as exc:  # noqa: BLE001 - one broken task must not erase other results
            results.append({"id": case["id"], "outcome": "infrastructure_error", "error": str(exc)})
    report = {"summary": summarize(results), "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
