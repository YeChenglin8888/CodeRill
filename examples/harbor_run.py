"""Preflight, one-task Harbor entry point, and offline result report."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path


def preflight(root: Path, harbor: str, env: dict[str, str]) -> list[dict[str, str | bool]]:
    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("source", all((root / name).exists() for name in ("coderill", "pyproject.toml", "examples/harbor_agent.py")),
        "CodeRill package and adapter")
    add("harbor", bool(shutil.which(harbor) or Path(harbor).is_file()), "Harbor CLI")
    try:
        docker = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                                capture_output=True, text=True, timeout=10, check=False)
        docker_ok = docker.returncode == 0 and bool(docker.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        docker_ok = False
    add("docker", docker_ok, "Docker daemon")
    for name in ("CODERILL_API_KEY", "CODERILL_MODEL", "CODERILL_PROVIDER"):
        add(name, bool(env.get(name, "").strip()), "configured" if env.get(name, "").strip() else "missing")
    add("provider", env.get("CODERILL_PROVIDER") in ("openai", "anthropic"), "openai or anthropic")
    return checks


def harbor_command(harbor: str, dataset: str, task: str, output: Path, env: dict[str, str]) -> list[str]:
    command = [harbor, "run", "--dataset", dataset, "--include-task-name", task,
               "--agent", "examples.harbor_agent:CodeRillHarborAgent",
               "--model", f"{env['CODERILL_PROVIDER']}/{env['CODERILL_MODEL']}",
               "--n-concurrent", "1", "--n-attempts", "1", "--jobs-dir", str(output), "--yes"]
    for name in ("CODERILL_API_KEY", "CODERILL_MODEL", "CODERILL_PROVIDER", "CODERILL_BASE_URL"):
        if env.get(name):
            command.extend(["--agent-env", f"{name}=${{{name}}}"])
    return command


def classify(result: dict) -> str:
    verifier = result.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    if isinstance(reward, (int, float)) and not isinstance(reward, bool):
        return "passed" if reward > 0 else "reward_zero"
    exception = result.get("exception_info")
    kind = str(exception.get("exception_type", "")) if isinstance(exception, dict) else ""
    if "timeout" in kind.lower():
        return "timeout"
    for phase in ("environment_setup", "agent_setup", "agent_execution", "verifier"):
        value = result.get(phase)
        if not isinstance(value, dict) or not value.get("finished_at"):
            return phase
    return "reward_missing"


def summarize(run_dir: Path) -> dict:
    trials = []
    for path in sorted(run_dir.rglob("result.json")):
        if path.parent.parent.name != "trials":
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise TypeError(f"expected object in {path}")
        trials.append({"trial": path.parent.name, "outcome": classify(result)})
    counts = dict(Counter(item["outcome"] for item in trials))
    return {"run_dir": str(run_dir), "total": len(trials), "explicit_rewards":
            counts.get("passed", 0) + counts.get("reward_zero", 0), "outcomes": counts, "trials": trials}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CodeRill Harbor one-task runner and offline reporter")
    parser.add_argument("action", choices=("preflight", "run", "report"))
    parser.add_argument("--harbor", default="harbor")
    parser.add_argument("--dataset", default="terminal-bench@2.0")
    parser.add_argument("--task", default="chess-best-move")
    parser.add_argument("--output", type=Path, default=Path("harbor-runs/coderill-smoke"))
    args = parser.parse_args(argv)
    if args.action == "report":
        print(json.dumps(summarize(args.output), ensure_ascii=False, indent=2))
        return 0
    root = Path(__file__).resolve().parents[1]
    checks = preflight(root, args.harbor, os.environ)
    print(json.dumps({"ok": all(item["ok"] for item in checks), "checks": checks}, ensure_ascii=False, indent=2))
    if not all(item["ok"] for item in checks):
        return 1
    if args.action == "preflight":
        return 0
    args.output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root), env.get("PYTHONPATH", ""))))
    result = subprocess.run(harbor_command(args.harbor, args.dataset, args.task, args.output, env),
                            cwd=root, env=env, check=False)
    print(json.dumps(summarize(args.output), ensure_ascii=False, indent=2))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
