"""Compare a light model with the primary model on held-out read-only tasks."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from coderill.agent.runtime import AgentRuntime
from coderill.config import Config
from coderill.permissions import Mode, ReviewManager
from coderill.providers import create_provider
from coderill.routing import light_profile_hash
from coderill.session import Session, SessionStore
from coderill.tools import Registry
from coderill.tools.advanced import ReadMultiTool
from coderill.tools.files import ReadFileTool
from coderill.tools.glob_tool import GlobTool
from coderill.tools.grep import GrepTool


def load_cases(path: Path) -> dict:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, dict):
        raise TypeError("qualification dataset must be an object")
    for kind, minimum in (("read_only", 100), ("boundaries", 50)):
        rows = cases.get(kind)
        if (not isinstance(rows, list) or len(rows) < minimum or
                any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in rows) or
                len({row["id"] for row in rows}) != len(rows)):
            raise ValueError(f"{kind} requires {minimum} uniquely identified cases")
    for row in cases["read_only"]:
        if not (isinstance(row.get("prompt"), str) and isinstance(row.get("expected"), str)
                and isinstance(row.get("files"), dict) and row["expected"]):
            raise ValueError("read_only case requires prompt, expected substring and files")
        for name, content in row["files"].items():
            if (not isinstance(name, str) or not name.strip() or not isinstance(content, str) or
                    Path(name).is_absolute() or ".." in Path(name).parts):
                raise ValueError("read_only case contains an unsafe file path")
    for row in cases["boundaries"]:
        if (row.get("decision") not in ("same", "new", "uncertain") or
                not isinstance(row.get("previous"), list) or not row["previous"] or
                any(not isinstance(message, dict) for message in row["previous"]) or
                not isinstance(row.get("latest"), str)):
            raise ValueError("boundary case requires previous messages, latest message and expected decision")
    return cases


async def read_only_case(config: Config, row: dict) -> bool:
    with tempfile.TemporaryDirectory(prefix="coderill-route-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        for name, content in row["files"].items():
            target = workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        current = replace(config, workspace=workspace, routing={"enabled": False}, max_turns=10)
        registry = Registry(workspace, current.sandbox_policy)
        for tool in (ReadFileTool(workspace), ReadMultiTool(workspace), GlobTool(workspace), GrepTool(workspace)):
            registry.register(tool)
        session = Session.new(current.model, str(workspace), SessionStore(root / "sessions"))
        runtime = AgentRuntime(current, session, registry, create_provider(current),
                               ReviewManager(workspace, mode=Mode.DONT_ASK))
        result = await runtime.run_turn(row["prompt"])
        return (result.status == "complete" and row["expected"] in result.text and
                any(item["status"] == "success" for item in session.finished_calls.values()))


async def boundary_case(config: Config, row: dict) -> bool:
    with tempfile.TemporaryDirectory(prefix="coderill-route-boundary-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        current = replace(config, workspace=workspace, routing={"enabled": False})
        session = Session.new(current.model, str(workspace), SessionStore(root / "sessions"))
        for message in row["previous"]:
            if message.get("role") not in ("user", "assistant") or not isinstance(message.get("content"), str):
                raise ValueError("boundary history accepts only user and assistant text")
            session.record(message)
        session.start_turn()
        session.record({"role": "user", "content": row["latest"]})
        runtime = AgentRuntime(current, session, Registry(workspace), create_provider(current),
                               ReviewManager(workspace, mode=Mode.DONT_ASK))
        runtime._turn_deadline = time.monotonic() + current.request_timeout
        await runtime._classify_task(row["latest"])
        return next(event for event in reversed(session.store.events(session.id))
                    if event["type"] == "task_boundary_observed")["decision"] == row["decision"]


async def qualify(primary: Config, light: Config, cases: dict) -> dict:
    scores = {"primary": {"read_only": 0, "boundary_errors": 0},
              "light": {"read_only": 0, "boundary_errors": 0}}
    for name, config in (("primary", primary), ("light", light)):
        for row in cases["read_only"]:
            scores[name]["read_only"] += await read_only_case(config, row)
        for row in cases["boundaries"]:
            scores[name]["boundary_errors"] += not await boundary_case(config, row)
    count = len(cases["read_only"])
    boundaries = len(cases["boundaries"])
    return {"read_only_count": count, "boundary_count": boundaries,
            "primary_pass_rate": scores["primary"]["read_only"] / count,
            "light_pass_rate": scores["light"]["read_only"] / count,
            "primary_boundary_error_rate": scores["primary"]["boundary_errors"] / boundaries,
            "light_boundary_error_rate": scores["light"]["boundary_errors"] / boundaries}


def main() -> None:
    parser = argparse.ArgumentParser(description="Qualify one configured same-provider light model.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--light-profile", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    primary = Config.from_env()
    profile = primary.profiles.get(args.light_profile)
    if not isinstance(profile, dict) or any(key not in profile for key in ("model", "vision", "max_context_tokens")):
        parser.error("light profile requires model, vision and max_context_tokens")
    if (profile.get("provider", primary.provider) != primary.provider or
            (profile.get("base_url") or primary.base_url or "").rstrip("/") != (primary.base_url or "").rstrip("/")):
        parser.error("light profile must use the primary provider and service address")
    light = replace(primary, model=profile["model"], vision=profile["vision"],
                    max_context_tokens=profile["max_context_tokens"],
                    thinking_enabled=profile.get("thinking_enabled", primary.thinking_enabled))
    result = asyncio.run(qualify(primary, light, cases))
    passed = (result["light_pass_rate"] >= result["primary_pass_rate"] - 0.02 and
              result["light_boundary_error_rate"] <= result["primary_boundary_error_rate"])
    report = {"status": "passed" if passed else "failed", "provider": primary.provider,
              "base_url": primary.base_url or "", "primary_model": primary.model,
              "light_model": light.model, "dataset": str(args.dataset.resolve()),
              "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
              "profile_sha256": light_profile_hash(profile, primary), **result}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
