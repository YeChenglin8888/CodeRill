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
