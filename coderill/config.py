"""Runtime configuration.

Everything is read from environment variables (optionally via a .env file) so
that no secrets ever live in the source tree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

DEFAULT_MODEL = "gpt-4o-mini"


def _toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import tomllib
    except ImportError:  # Python 3.10
        import tomli as tomllib
    with path.open("rb") as handle:
        return tomllib.load(handle)


@dataclass
class Config:
    """Resolved settings for one agent run."""

    api_key: str = ""
    provider: str = "openai"
    model: str = DEFAULT_MODEL
    base_url: str | None = None
    vision: bool = False
    thinking_enabled: bool = True
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    max_tokens: int = 4096
    temperature: float = 0.0
    max_turns: int = 30
    max_tool_rounds: int = 200
    max_turn_seconds: float = 3600
    max_retries: int = 4
    request_timeout: int = 120
    max_context_tokens: int = 128_000
    benchmark: bool = False
    # Jev / browser-task settings.
    browser_enabled: bool = False
    browser_channel: str = "msedge"
    typesafe_api_key: str = ""
    typesafe_model: str = "jev-latest"
    workspace: Path = field(default_factory=Path.cwd)

    @classmethod
    def from_env(
        cls,
        workspace: str | Path | None = None,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> Config:
        """Merge defaults, global/project TOML, environment and CLI overrides."""
        load_dotenv()
        ws = Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()
        values: dict[str, Any] = {"workspace": ws}
        profiles: dict[str, dict[str, Any]] = {}
        for path in (Path.home() / ".coderill" / "config.toml", ws / ".coderill.toml"):
            data = _toml(path)
            values.update({key: value for key, value in data.items() if key in cls.__dataclass_fields__ and key != "profiles"})
            profiles.update(data.get("models", {}))
        values["profiles"] = profiles
        selected = (overrides or {}).get("profile") or os.getenv("CODERILL_PROFILE")
        if selected:
            if selected not in profiles:
                raise ValueError(f"unknown model profile '{selected}'")
            profile = profiles[selected]
            values.update({key: value for key, value in profile.items() if key in cls.__dataclass_fields__})
            if profile.get("api_key_env"):
                values["api_key"] = os.getenv(str(profile["api_key_env"]), "")
        env_values: dict[str, tuple[str, Any]] = {
            "provider": ("CODERILL_PROVIDER", str),
            "thinking_enabled": ("CODERILL_THINKING_ENABLED", lambda value: value == "1"),
            "model": ("CODERILL_MODEL", str),
            "base_url": ("CODERILL_BASE_URL", str),
            "max_tokens": ("CODERILL_MAX_TOKENS", int),
            "temperature": ("CODERILL_TEMPERATURE", float),
            "max_turns": ("CODERILL_MAX_TURNS", int),
            "max_tool_rounds": ("CODERILL_MAX_TOOL_ROUNDS", int),
            "max_turn_seconds": ("CODERILL_MAX_TURN_SECONDS", float),
            "request_timeout": ("CODERILL_REQUEST_TIMEOUT", int),
            "max_context_tokens": ("CODERILL_MAX_CONTEXT", int),
            "browser_channel": ("CODERILL_BROWSER_CHANNEL", str),
            "typesafe_model": ("TYPESAFE_MODEL", str),
        }
        for field_name, (name, convert) in env_values.items():
            if os.getenv(name) is not None:
                values[field_name] = convert(os.environ[name])
        values["browser_enabled"] = os.getenv("CODERILL_BROWSER_ENABLED") == "1" or values.get("browser_enabled", False)
        values["typesafe_api_key"] = os.getenv("TYPESAFE_API_KEY", values.get("typesafe_api_key", ""))
        values.update({key: value for key, value in (overrides or {}).items() if key != "profile" and value is not None})
        provider = values.get("provider", "openai")
        if provider not in ("openai", "anthropic"):
            raise ValueError(f"unknown provider '{provider}'")
        if provider == "openai" and "base_url" not in (overrides or {}):
            values["base_url"] = os.getenv("CODERILL_BASE_URL") or os.getenv("OPENAI_BASE_URL") or values.get("base_url")
        if not values.get("api_key"):
            names = ("CODERILL_API_KEY", "ANTHROPIC_API_KEY") if provider == "anthropic" else (
                "CODERILL_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"
            )
            values["api_key"] = next((os.environ[name] for name in names if os.getenv(name)), "")
        if not values["api_key"]:
            raise RuntimeError(f"No API key found for {provider}; set CODERILL_API_KEY or the provider API key.")
        return cls(**values)
