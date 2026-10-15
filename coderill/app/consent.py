"""Interactive consent prompt and file change preview."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from rich.console import Console

from ..review import build_prewrite_review
from .state import _short


def make_asker(console: Console, root_getter: Callable[[], Path]):
    def ask(tool_name: str, arguments: dict):
        preview = _short(json.dumps(arguments, ensure_ascii=False), 300)
        console.print(f"\n[yellow]allow[/yellow] {tool_name}? [dim]{preview}[/dim]")
        review = build_prewrite_review(tool_name, arguments, root_getter())
        if review:
            console.print(review, style="dim")
        answer = console.input("  [y]es / [n]o / [a]lways > ").strip().lower()
        if answer == "a":
            return "always"
        return answer.startswith("y")

    return ask
