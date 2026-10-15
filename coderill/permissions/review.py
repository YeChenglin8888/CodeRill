"""Durable review requests independent of the terminal interface."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from ..review import build_prewrite_review
from ..tools.base import Tool, resolve_path
from .policy import GrantStore, Mode, Request, classify, default_scope


class ReviewManager:
    def __init__(self, workspace: Path, *, mode: Mode = Mode.STANDARD, store: GrantStore | None = None) -> None:
        self.workspace = workspace.resolve()
        self.mode = mode
        self.store = store or GrantStore()

    @property
    def allow_all(self) -> bool:
        return self.mode is Mode.BYPASS

    @allow_all.setter
    def allow_all(self, enabled: bool) -> None:
        self.mode = Mode.BYPASS if enabled else Mode.STANDARD

    def request(self, tool: Tool, call: dict[str, Any]) -> dict[str, Any] | None:
        """Return a review for calls that cannot run without a new decision."""
        if not tool.needs_consent or self.mode is Mode.BYPASS:
            return None
        name = call["name"]
        args = call["arguments"]
        target = self._target(name, args)
        permission = classify(name, args, str(self.workspace)) or Request(name, "tool", "", str(self.workspace))
        if target is not None:
            permission = Request(name, "filesystem", str(target), str(self.workspace))
        if self.store.match(permission) is not None:
            return None
        if self.mode is Mode.AGGRESSIVE and permission.action in ("filesystem", "shell"):
            return None
        return self._build(call, target)

    def refresh(self, review: dict[str, Any]) -> dict[str, Any] | None:
        """A changed file invalidates the old review and gets a fresh id and diff."""
        call = review["call"]
        target = self._target(call["name"], call["arguments"])
        if str(target or "") == review["target"] and self._digest(target) == review["digest"]:
            return None
        return self._build(call, target)

    def remember(self, review: dict[str, Any]) -> None:
        call = review["call"]
        permission = classify(call["name"], call["arguments"], str(self.workspace))
        if permission is None:
            return
        if review["target"]:
            permission = Request(call["name"], "filesystem", review["target"], str(self.workspace))
        scope = default_scope(permission)
        if scope is not None:
            self.store.add(scope)

    def _build(self, call: dict[str, Any], target: Path | None) -> dict[str, Any]:
        return {
            "id": uuid.uuid4().hex,
            "call": call,
            "target": str(target or ""),
            "digest": self._digest(target),
            "diff": build_prewrite_review(call["name"], call["arguments"], self.workspace),
            "workspace": str(self.workspace),
        }

    def _target(self, name: str, arguments: dict[str, Any]) -> Path | None:
        if name not in ("write_file", "edit_file"):
            return None
        key = "path" if name == "write_file" else "file_path"
        return resolve_path(self.workspace, str(arguments[key]))

    @staticmethod
    def _digest(target: Path | None) -> str:
        if target is None:
            return ""
        if not target.exists():
            return "missing"
        return hashlib.sha256(target.read_bytes()).hexdigest()
