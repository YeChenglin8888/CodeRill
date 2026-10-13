"""Compatibility shim.

The agent loop now lives in :mod:`coderill.agent`. This module is kept only so
that an old ``from coderill.loop import Agent`` still resolves; it can be deleted.
"""

from .agent import Agent, DeltaCallback, ResultCallback, ToolCallback

__all__ = ["Agent", "DeltaCallback", "ResultCallback", "ToolCallback"]
