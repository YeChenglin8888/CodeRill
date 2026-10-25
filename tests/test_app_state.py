from rich.console import Console

from coderill.app.state import State
from coderill.config import Config
from coderill.hooks import Hooks
from coderill.permissions import Mode, ReviewManager
from coderill.session import Session, SessionStore


def test_rebuild_uses_freshly_loaded_session_projection(tmp_path, monkeypatch):
    monkeypatch.setattr("coderill.app.state.create_provider", lambda config: object())
    store = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), store)
    state = State(
        Config(api_key="unused", workspace=tmp_path), session, Console(), object(),
        ReviewManager(tmp_path, mode=Mode.BYPASS), Hooks([], []),
    )
    try:
        state.rebuild()
        session.record({"role": "user", "content": "new event"})
        loaded = Session.load(session.id, store)
        state.session = loaded
        state.rebuild()
        assert state.session is loaded
        assert state.subagent_services[session.id].parent is loaded
        assert state.subagent_services[session.id].background.session is loaded
        assert state.subagent_services[session.id].plan is state.agent.plan
        assert state.agent.collect_background is not None
    finally:
        state.close()


def test_model_switch_is_shared_and_keeps_old_state_on_failure(tmp_path, monkeypatch):
    config = Config(api_key="unused", workspace=tmp_path, benchmark=True, sandbox="docker",
                    sandbox_network="none")
    session = Session.new("old", str(tmp_path), SessionStore(tmp_path / "sessions"))
    state = State(config, session, Console(), object(), ReviewManager(tmp_path, mode=Mode.BYPASS), Hooks([], []))
    monkeypatch.setattr("coderill.app.state.create_provider", lambda config: object())
    monkeypatch.setattr("coderill.app.state.LLM", lambda config: object())
    try:
        state.rebuild()
        monkeypatch.setattr(Config, "from_env", lambda *args, **kwargs: Config(
            api_key="unused", workspace=tmp_path, model="new", benchmark=kwargs["overrides"]["benchmark"]
        ))
        state.select_model("other")
        assert state.config.model == session.model == "new"
        assert state.config.benchmark
        assert state.config.sandbox == "docker"
        assert state.config.sandbox_network == "none"
        old_agent = state.agent
        monkeypatch.setattr("coderill.app.state.create_provider", lambda config: (_ for _ in ()).throw(ValueError("bad")))
        try:
            state.select_model("broken")
        except ValueError:
            pass
        assert state.agent is old_agent
        assert state.session.model == "new"
    finally:
        state.close()
