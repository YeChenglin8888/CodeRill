"""One assembly path for provider, permissions, tools, and session state."""

from __future__ import annotations

from rich.console import Console

from ..config import Config
from ..hooks import load_hooks
from ..llm import LLM
from ..permissions import Mode, ReviewManager
from ..sandbox import recover_orphan_jobs
from ..session import Session
from .state import State


def build_state(config: Config, session: Session, console: Console, *, auto_approve: bool, plan: bool) -> State:
    recover_orphan_jobs()
    permission = ReviewManager(config.workspace, mode=Mode.BYPASS if auto_approve else Mode.STANDARD,
                               policy=config.sandbox_policy)
    state = State(
        config=config,
        session=session,
        console=console,
        llm=LLM(config),
        permission=permission,
        hooks=load_hooks(),
        plan_mode=plan,
    )
    state.rebuild()
    return state
