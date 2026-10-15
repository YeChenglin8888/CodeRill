from coderill.config import Config


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
    monkeypatch.setattr("coderill.config.load_dotenv", lambda: None)
    try:
        Config.from_env(tmp_path, overrides={"provider": "anthropic"})
    except RuntimeError as exc:
        assert "anthropic" in str(exc)
    else:
        raise AssertionError("missing key was accepted")
