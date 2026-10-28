"""Desktop actions, review boundary, and screenshot delivery to the model."""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from coderill.agent.runtime import AgentRuntime
from coderill.config import Config
from coderill.permissions import GrantStore, Mode, ReviewManager
from coderill.providers.types import ChatResponse, ChatStreamEvent, ToolCall
from coderill.providers.wire import openai_messages
from coderill.session import Session, SessionStore
from coderill.tools import build_registry, computer
from coderill.tools.base import ToolOutput


class Provider:
    def __init__(self):
        self.requests = []
        self.replies = [ChatResponse("", (ToolCall("screen", "computer_use", {"action": "screenshot"}),)),
                        ChatResponse("screen observed")]

    async def stream(self, request):
        self.requests.append(request)
        yield ChatStreamEvent("completed", response=self.replies.pop(0))


@pytest.fixture
def desktop(monkeypatch):
    calls = []

    class Image:
        def save(self, buffer, format):
            assert format == "PNG"
            buffer.write(b"screen-image")

    gui = SimpleNamespace(
        size=lambda: (800, 600), screenshot=lambda: Image(),
        KEYBOARD_KEYS=("ctrl", "a", "esc"),
        click=lambda *args: calls.append(("click", args)),
        hotkey=lambda *args: calls.append(("hotkey", args)),
    )
    monkeypatch.setattr(computer, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(sys.modules, "pyautogui", gui)
    return calls


def test_computer_action_validates_before_input_and_returns_image(desktop):
    tool = computer.ComputerUseTool()
    assert tool.execute("click", 10, 20) == ToolOutput(
        "click complete. Current primary display: 800x600. Screenshot attached.", b"screen-image")
    assert desktop == [("click", (10, 20))]
    assert tool.execute("keypress", key="ctrl+a").image == b"screen-image"
    assert desktop[-1] == ("hotkey", ("ctrl", "a"))
    with pytest.raises(Exception, match="coordinates"):
        tool.execute("click", 800, 20)
    with pytest.raises(Exception, match="ASCII"):
        tool.execute("type", text="中文")
    assert len(desktop) == 2


def test_computer_registration_requires_opt_in_and_review(tmp_path):
    config = Config(api_key="unused", workspace=tmp_path, computer_enabled=True,
                    vision=True, typesafe_api_key="unused")
    registry = build_registry(tmp_path, config=config)
    assert {"computer_use", "jev_decide"} <= set(registry.names())
    manager = ReviewManager(tmp_path, mode=Mode.AGGRESSIVE, store=GrantStore(tmp_path / "grants.json"))
    call = {"id": "c", "name": "computer_use", "arguments": {"action": "screenshot"}}
    review = manager.request(registry.get("computer_use"), call)
    assert review is not None
    manager.remember(review)
    assert manager.request(registry.get("computer_use"), call) is None
    assert ReviewManager(tmp_path, store=GrantStore(tmp_path / "grants.json")).request(
        registry.get("computer_use"), call) is not None


def test_computer_screenshot_reaches_next_model_request(tmp_path, desktop):
    config = Config(api_key="unused", model="fake", workspace=tmp_path, computer_enabled=True,
                    vision=True, typesafe_api_key="unused", max_turns=3)
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    provider = Provider()
    runner = AgentRuntime(config, session, build_registry(tmp_path, config=config), provider,
                          ReviewManager(tmp_path, mode=Mode.BYPASS,
                                        store=GrantStore(tmp_path / "grants.json")))
    result = asyncio.run(runner.run_turn("observe the desktop"))
    assert result.status == "complete", result.error
    messages = openai_messages(provider.requests[1])
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"][1]["type"] == "image_url"
    assert session.store.read_blob(session.id, session.messages[-2]["attachments"][0]["digest"]) == b"screen-image"
