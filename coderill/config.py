"""Runtime configuration.

Everything is read from environment variables (optionally via a .env file) so
that no secrets ever live in the source tree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .sandbox import SandboxPolicy, parse_ports

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
    routing: dict[str, Any] = field(default_factory=lambda: {"enabled": True})
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
    computer_enabled: bool = False
    browser_channel: str = "msedge"
    typesafe_api_key: str = ""
    typesafe_model: str = "jev-latest"
    jev_decisions_enabled: bool = False
    workspace: Path = field(default_factory=Path.cwd)
    sandbox: str = "off"
    sandbox_profile: str = "workspace-write"
    sandbox_network: str = "restricted"
    sandbox_image: str = "coderill-sandbox:local"
    sandbox_ports: tuple[tuple[int, int], ...] = ()
    deny_read_paths: tuple[str, ...] = ()
    sandbox_cpus: float = 2.0
    sandbox_memory: str = "2g"
    sandbox_pids: int = 256

    @cached_property
    def sandbox_policy(self) -> SandboxPolicy:
        return SandboxPolicy(self.workspace, backend=self.sandbox, profile=self.sandbox_profile,
                             network=self.sandbox_network, image=self.sandbox_image, ports=self.sandbox_ports,
                             deny_read_paths=self.deny_read_paths, cpus=self.sandbox_cpus,
                             memory=self.sandbox_memory, pids=self.sandbox_pids)

    @classmethod
    def from_env(
        cls,
        workspace: str | Path | None = None,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> Config:
        """Merge defaults, global/project TOML, environment and CLI overrides."""
        ws = Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()
        global_env = Path.home() / ".coderill" / ".env"
        global_env.parent.mkdir(parents=True, exist_ok=True)
        template = Path(__file__).with_name("env.example").read_text(encoding="utf-8")
        try:
            fd = os.open(global_env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if global_env.stat().st_size == 0:
                global_env.write_text(template, encoding="utf-8")
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(template)
        # Project dotenv and model profiles are untrusted for sandbox settings.
        sandbox_env = {name: os.environ.get(name) for name in (
            "CODERILL_SANDBOX", "CODERILL_SANDBOX_PROFILE", "CODERILL_SANDBOX_NETWORK",
            "CODERILL_SANDBOX_IMAGE", "CODERILL_SANDBOX_PORTS")}
        load_dotenv(ws / ".env")
        load_dotenv(global_env)
        for name, original in sandbox_env.items():
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original
        values: dict[str, Any] = {"workspace": ws}
        profiles: dict[str, dict[str, Any]] = {}
        routing: dict[str, Any] = {"enabled": True}
        sandbox_fields = {"sandbox", "sandbox_profile", "sandbox_network", "sandbox_image", "sandbox_ports",
                          "deny_read_paths", "sandbox_cpus", "sandbox_memory", "sandbox_pids"}
        for path in (Path.home() / ".coderill" / "config.toml", ws / ".coderill.toml"):
            data = _toml(path)
            values.update({key: value for key, value in data.items()
                           if key in cls.__dataclass_fields__ and key not in ("profiles", "routing", "workspace")
                           and (key not in sandbox_fields or path.parent == global_env.parent)})
            profiles.update({name: profile for name, profile in data.get("models", {}).items()
                             if path.parent == global_env.parent or name != routing.get("light_profile")})
            if path.parent == global_env.parent:
                routing.update(data.get("routing", {}))
        values["profiles"] = profiles
        values["routing"] = routing
        selected = (overrides or {}).get("profile") or os.getenv("CODERILL_PROFILE")
        if selected:
            if selected not in profiles:
                raise ValueError(f"unknown model profile '{selected}'")
            profile = profiles[selected]
            values.update({key: value for key, value in profile.items()
                           if key in cls.__dataclass_fields__ and key not in sandbox_fields | {"workspace", "routing"}})
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
        values["computer_enabled"] = os.getenv("CODERILL_COMPUTER_ENABLED") == "1" or values.get("computer_enabled", False)
        values["vision"] = os.getenv("CODERILL_VISION") == "1" or values.get("vision", False)
        values["jev_decisions_enabled"] = (os.getenv("CODERILL_JEV_DECISIONS") == "1" or
                                            values.get("jev_decisions_enabled", False))
        values["typesafe_api_key"] = os.getenv("TYPESAFE_API_KEY", values.get("typesafe_api_key", ""))
        for field_name, name in (("sandbox", "CODERILL_SANDBOX"),
                                 ("sandbox_profile", "CODERILL_SANDBOX_PROFILE"),
                                 ("sandbox_network", "CODERILL_SANDBOX_NETWORK"),
                                 ("sandbox_image", "CODERILL_SANDBOX_IMAGE")):
            if sandbox_env[name] is not None:
                values[field_name] = sandbox_env[name]
        if sandbox_env["CODERILL_SANDBOX_PORTS"]:
            values["sandbox_ports"] = parse_ports(sandbox_env["CODERILL_SANDBOX_PORTS"].split(","))
        values.update({key: value for key, value in (overrides or {}).items() if key != "profile" and value is not None})
        routing = values["routing"]
        if not isinstance(routing, dict) or not isinstance(routing.get("enabled", True), bool):
            raise TypeError("routing.enabled must be a boolean")
        for name in ("light_profile", "qualification_report"):
            if name in routing and (not isinstance(routing[name], str) or not routing[name].strip()):
                raise ValueError(f"routing.{name} must be a non-empty string")
        embedding = routing.get("embedding")
        if embedding is not None and (not isinstance(embedding, dict) or
                                      not all(isinstance(embedding.get(key), str) and embedding[key]
                                              for key in ("base_url", "model", "api_key_env"))):
            raise ValueError("routing.embedding requires base_url, model and api_key_env")
        if isinstance(values.get("sandbox_ports"), list):
            values["sandbox_ports"] = parse_ports(values["sandbox_ports"])
        if isinstance(values.get("deny_read_paths"), list):
            values["deny_read_paths"] = tuple(values["deny_read_paths"])
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
            key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
            raise RuntimeError(f"No API key found for {provider}. Add {key_name}=<your-key> to {global_env}, "
                               "or set CODERILL_API_KEY in your environment.")
        config = cls(**values)
        if config.computer_enabled and (not config.vision or not config.typesafe_api_key):
            raise ValueError("computer use requires CODERILL_VISION=1 and TYPESAFE_API_KEY")
        if config.sandbox == "docker" and workspace and Path(workspace).expanduser().is_symlink():
            raise ValueError("sandbox workspace root cannot be a symlink")
        _ = config.sandbox_policy  # validate before a turn can run
        return config
