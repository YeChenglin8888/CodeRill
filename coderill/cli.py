"""Compatibility entry point for the terminal application."""

from .app.cli import main, parse_args

__all__ = ["main", "parse_args"]
