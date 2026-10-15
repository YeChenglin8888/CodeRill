"""Opt-in real-model smoke evaluation; reports status, duration, and usage."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from rich.console import Console

from coderill.app.factory import build_state
from coderill.config import Config
from coderill.session import Session


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one real-model CodeRill turn and emit a JSON result.")
    parser.add_argument("message", help="Task prompt; use a read-only task for a smoke check.")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--provider", choices=("openai", "anthropic"))
    args = parser.parse_args()
    config = Config.from_env(args.cwd, overrides={"provider": args.provider})
    session = Session.new(config.model, str(config.workspace))
    state = build_state(config, session, Console(quiet=True), auto_approve=False, plan=False)
    state.agent.on_event = lambda _event: None
    started = time.monotonic()
    try:
        result = state.run_async(state.agent.run_turn(args.message))
    finally:
        state.close()
    report = {
        "status": result.status,
        "text": result.text,
        "error": result.error,
        "review_id": result.review["id"] if result.review else None,
        "session_id": session.id,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "usage": session.usage,
    }
    assert report["status"] in ("complete", "waiting", "cancelled", "failed", "budget")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
