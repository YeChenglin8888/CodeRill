"""Compatibility shim.

The system prompt now lives in :mod:`coderill.prompt`. This module is kept only
so that an old ``from coderill.prompts import system_prompt`` still resolves; it
can be deleted.
"""

from .prompt import PLAN_MODE_PROMPT, system_prompt

__all__ = ["PLAN_MODE_PROMPT", "system_prompt"]
