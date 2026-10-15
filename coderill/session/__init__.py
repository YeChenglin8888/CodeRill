"""Durable conversation events and session lifecycle."""

from pathlib import Path

SESSION_DIR = Path.home() / ".coderill" / "sessions"

from .model import Session
from .store import SessionStore

__all__ = ["SESSION_DIR", "Session", "SessionStore"]
