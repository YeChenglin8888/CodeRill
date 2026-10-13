"""Runtime configuration.

Everything is read from environment variables (optionally via a .env file) so
that no secrets ever live in the source tree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "gpt-4o-mini"


@dataclass
class Config:
    """Resolved settings for one agent run."""

    api_key: str = ""
    model: str = DEFAULT_MODEL
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    max_turns: int = 30
    max_retries: int = 4
    max_context_tokens: int = 128_000
    # Jev / browser-task settings.
    browser_enabled: bool = False
    browser_channel: str = "msedge"
    typesafe_api_key: str = ""
    typesafe_model: str = "jev-latest"
    workspace: Path = field(default_factory=Path.cwd)

    @classmethod
    def from_env(cls, workspace: str | Path | None = None) -> Config:
        """Build a config from the environment, raising if the key is missing."""
        load_dotenv()

        api_key = (
            os.getenv("CODERILL_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or ""
        )
        if not api_key:
            raise RuntimeError(
                "No API key found. Set OPENAI_API_KEY in your environment or in a .env file "
                "(see .env.example)."
            )

        ws = Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()

        return cls(
            api_key=api_key,
            model=os.getenv("CODERILL_MODEL", DEFAULT_MODEL),
            base_url=os.getenv("OPENAI_BASE_URL") or os.getenv("CODERILL_BASE_URL") or None,
            max_tokens=int(os.getenv("CODERILL_MAX_TOKENS", "4096")),
            temperature=float(os.getenv("CODERILL_TEMPERATURE", "0")),
            max_turns=int(os.getenv("CODERILL_MAX_TURNS", "30")),
            max_context_tokens=int(os.getenv("CODERILL_MAX_CONTEXT", "128000")),
            browser_enabled=os.getenv("CODERILL_BROWSER_ENABLED") == "1",
            browser_channel=os.getenv("CODERILL_BROWSER_CHANNEL", "msedge"),
            typesafe_api_key=os.getenv("TYPESAFE_API_KEY", ""),
            typesafe_model=os.getenv("TYPESAFE_MODEL", "jev-latest"),
            workspace=ws,
        )
