import os
from pathlib import Path

import pytest

from coderill.config import Config


@pytest.fixture(autouse=True)
def isolate_home(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")


def test_config_layers_and_model_profile(monkeypatch, tmp_path):
    home = tmp_path / "home"
    global_dir = home / ".coderill"
    global_dir.mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    (global_dir / "config.toml").write_text('model = "global"\nmax_turns = 5\n')
    (project / ".coderill.toml").write_text(
        'model = "project"\n[models.fast]\nprovider = "anthropic"\nmodel = "profile"\napi_key_env = "TEST_PROFILE_KEY"\n'
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setenv("TEST_PROFILE_KEY", "secret")
    monkeypatch.setenv("CODERILL_MODEL", "environment")
    config = Config.from_env(project, overrides={"profile": "fast", "model": "cli"})
    assert config.provider == "anthropic"
    assert config.model == "cli"
    assert config.max_turns == 5
    assert config.api_key == "secret"


def test_missing_provider_key_is_explicit(monkeypatch, tmp_path):
    for name in ("CODERILL_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    try:
        Config.from_env(tmp_path, overrides={"provider": "anthropic"})
    except RuntimeError as exc:
        assert "anthropic" in str(exc)
    else:
        raise AssertionError("missing key was accepted")


def test_global_env_works_from_any_workspace(monkeypatch, tmp_path):
    for name in ("CODERILL_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    global_env = tmp_path / "home" / ".coderill" / ".env"

    with pytest.raises(RuntimeError) as exc:
        Config.from_env(workspace)
    assert str(global_env) in str(exc.value)
    assert "OPENAI_API_KEY" in str(exc.value)
    assert global_env.is_file()
    assert global_env.read_text(encoding="utf-8") == (Path(__file__).parents[1] / ".env.example").read_text(encoding="utf-8")
    assert global_env.read_text(encoding="utf-8") == (Path(__file__).parents[1] / "coderill" / "env.example").read_text(encoding="utf-8")

    global_env.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError):
        Config.from_env(workspace)
    assert "# OPENAI_API_KEY=your-key" in global_env.read_text(encoding="utf-8")

    global_env.write_text("OPENAI_API_KEY=global-key\n", encoding="utf-8")
    assert Config.from_env(workspace).api_key == "global-key"
    assert global_env.read_text(encoding="utf-8") == "OPENAI_API_KEY=global-key\n"
    (workspace / ".env").write_text("OPENAI_API_KEY=project-key\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY")
    assert Config.from_env(workspace).api_key == "project-key"
    monkeypatch.setenv("OPENAI_API_KEY", "shell-key")
    assert Config.from_env(workspace).api_key == "shell-key"


def test_coderill_endpoint_overrides_generic_openai_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("CODERILL_API_KEY", "unused")
    monkeypatch.setenv("CODERILL_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    config = Config.from_env(tmp_path, overrides={"provider": "openai", "model": "deepseek-flash"})
    assert config.base_url == "https://api.deepseek.com"


@pytest.mark.parametrize("enabled", ["1", "0"])
def test_thinking_switch_reaches_both_clients(monkeypatch, tmp_path, enabled):
    from coderill.providers import LLM, create_provider
    from coderill.providers.types import ChatRequest

    monkeypatch.setenv("CODERILL_API_KEY", "unused")
    monkeypatch.setenv("CODERILL_THINKING_ENABLED", enabled)
    monkeypatch.setattr("openai.AsyncOpenAI", lambda **kwargs: object())
    monkeypatch.setattr("coderill.providers.openai_compatible.OpenAI", lambda **kwargs: object())
    config = Config.from_env(tmp_path, overrides={"provider": "openai", "base_url": "https://api.deepseek.com"})
    expected = {"thinking": {"type": "enabled" if enabled == "1" else "disabled"}}
    assert config.thinking_enabled == (enabled == "1")
    assert create_provider(config)._params(ChatRequest(messages=()))["extra_body"] == expected
    assert LLM(config)._base_kwargs([], None)["extra_body"] == expected
