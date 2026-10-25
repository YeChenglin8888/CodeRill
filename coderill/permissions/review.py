"""Durable review requests independent of the terminal interface."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from ..review import build_prewrite_review
from ..sandbox import SandboxPolicy, check_active_path, policy_scope
from ..tools.advanced import patch_targets
from ..tools.base import Tool, resolve_path
from .policy import GrantStore, Mode, Request, classify, default_scope


class ReviewManager:
    def __init__(self, workspace: Path, *, mode: Mode = Mode.STANDARD, store: GrantStore | None = None,
                 policy: SandboxPolicy | None = None) -> None:
        self.workspace = workspace.resolve()
        self.policy = policy
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
        with policy_scope(self.policy):
            targets = self._targets(name, args)
        target = targets[0] if len(targets) == 1 else None
        permission = classify(name, args, str(self.workspace)) or Request(name, "tool", "", str(self.workspace))
        if target is not None and permission.action == "filesystem":
            permission = Request(name, "filesystem", str(target), str(self.workspace))
        if self.store.match(permission) is not None:
            return None
        if self.mode is Mode.AGGRESSIVE and permission.action in ("filesystem", "shell"):
            return None
        return self._build(call, targets)

    def refresh(self, review: dict[str, Any]) -> dict[str, Any] | None:
        """A changed file invalidates the old review and gets a fresh id and diff."""
        call = review["call"]
        with policy_scope(self.policy):
            targets = self._targets(call["name"], call["arguments"])
            current = {str(target): self._digest(target) for target in targets}
        saved = review.get("targets", {review["target"]: review["digest"]} if review.get("target") else {})
        if current == saved:
            return None
        return self._build(call, targets)

    def remember(self, review: dict[str, Any]) -> None:
        call = review["call"]
        permission = classify(call["name"], call["arguments"], str(self.workspace))
        if permission is None:
            return
        if review["target"] and permission.action == "filesystem":
            permission = Request(call["name"], "filesystem", review["target"], str(self.workspace))
        scope = default_scope(permission)
        if scope is not None:
            self.store.add(scope)

    def _build(self, call: dict[str, Any], targets: list[Path]) -> dict[str, Any]:
        target = targets[0] if len(targets) == 1 else None
        with policy_scope(self.policy):
            return {
                "id": uuid.uuid4().hex,
                "call": call,
                "target": str(target or ""),
                "digest": self._digest(target),
                "targets": {str(item): self._digest(item) for item in targets},
                "diff": build_prewrite_review(call["name"], call["arguments"], self.workspace, self.policy),
                "workspace": str(self.workspace),
            }

    def _targets(self, name: str, arguments: dict[str, Any]) -> list[Path]:
        if name == "apply_patch":
            return patch_targets(self.workspace, str(arguments["patch"]), self.policy)
        if name in ("write_file", "delete_path"):
            return [resolve_path(self.workspace, str(arguments["path"]))]
        if name == "edit_file":
            return [resolve_path(self.workspace, str(arguments["file_path"]))]
        return []

    @staticmethod
    def _digest(target: Path | None) -> str:
        if target is None:
            return ""
        if not target.exists():
            return "missing"
        if target.is_file():
            check_active_path(target)
            return hashlib.sha256(target.read_bytes()).hexdigest()
        digest = hashlib.sha256()
        for item in sorted(target.rglob("*")):
            check_active_path(item)
            digest.update(item.relative_to(target).as_posix().encode())
            if item.is_symlink():
                digest.update(str(item.readlink()).encode())
            elif item.is_file():
                digest.update(item.read_bytes())
        return digest.hexdigest()
