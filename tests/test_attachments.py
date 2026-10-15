import asyncio

from coderill.agent.runtime import AgentRuntime
from coderill.config import Config
from coderill.permissions import GrantStore, ReviewManager
from coderill.providers.types import ChatResponse, ChatStreamEvent, ProviderCapabilities
from coderill.session import Session, SessionStore
from coderill.tools import build_registry


class Provider:
    model = "text-only"
    capabilities = ProviderCapabilities(vision=False)

    def __init__(self):
        self.request = None

    async def stream(self, request):
        self.request = request
        yield ChatStreamEvent("completed", response=ChatResponse("done"))


def runner(tmp_path, session, provider):
    return AgentRuntime(
        Config(api_key="unused", workspace=tmp_path), session, build_registry(tmp_path), provider,
        ReviewManager(tmp_path, store=GrantStore(tmp_path / "grants.json")),
    )


def test_text_attachment_survives_restart_and_reaches_provider(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), store)
    path = tmp_path / "notes.txt"
    path.write_text("hello from file")
    attachment = session.attach_file(path)
    provider = Provider()
    loaded = Session.load(session.id, store)
    result = asyncio.run(runner(tmp_path, loaded, provider).run_turn("summarize", [attachment]))
    assert result.status == "complete"
    assert "hello from file" in provider.request.messages[-1].parts[0].text


def test_image_requires_visual_model(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), store)
    path = tmp_path / "image.png"
    path.write_bytes(b"not-a-real-image")
    attachment = session.attach_file(path)
    result = asyncio.run(runner(tmp_path, session, Provider()).run_turn("describe", [attachment]))
    assert result.status == "failed"
    assert "does not support image" in result.error
    assert session.messages == []
