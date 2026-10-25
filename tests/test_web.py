"""Browser API contract checks without a paid model call."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from rich.console import Console

from coderill.agent.runtime import TurnResult
from coderill.app import web
from coderill.app.state import State
from coderill.config import Config
from coderill.hooks import Hooks
from coderill.llm import LLM
from coderill.mcp import MCPManager
from coderill.permissions import GrantStore, Mode, ReviewManager
from coderill.providers.types import ChatResponse, ChatStreamEvent, ProviderCapabilities, ToolCall
from coderill.session import Session, SessionStore


@pytest.mark.parametrize("assets_ready", [False, True])
def test_web_health_and_local_origin_boundary(tmp_path, monkeypatch, assets_ready):
    assets = tmp_path / "dist"
    assets.mkdir()
    if assets_ready:
        (assets / "index.html").write_text("<!doctype html><title>CodeRill</title>", encoding="utf-8")
    monkeypatch.setattr(web, "DIST", assets)
    server = web.WebServer(("127.0.0.1", 0), Config(workspace=tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        if assets_ready:
            with urlopen(base + "/healthz") as response:
                assert json.load(response)["status"] == "ok"
        else:
            with pytest.raises(HTTPError) as error:
                urlopen(base + "/healthz")
            assert error.value.code == 503
        for headers in ({"Origin": "https://untrusted.example"}, {"Host": "untrusted.example"}):
            with pytest.raises(HTTPError) as error:
                urlopen(Request(base + "/healthz", headers=headers))
            assert error.value.code == 403
        server.config.workspace = tmp_path / "missing"
        with pytest.raises(HTTPError) as error:
            urlopen(base + "/healthz")
        assert error.value.code == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_web_sessions_commands_stream_and_upload(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions")

    class LocalSession:
        @staticmethod
        def new(model, workspace):
            return Session.new(model, workspace, store)

        @staticmethod
        def load(session_id):
            return Session.load(session_id, store)

        @staticmethod
        def list_all():
            return Session.list_all(store)

    monkeypatch.setattr(web, "Session", LocalSession)
    workspace = tmp_path / "project"
    workspace.mkdir()
    config = Config(api_key="unused", model="fake", workspace=workspace)

    class FakeState:
        def __init__(self, session, *, plan=False, auto_approve=False):
            self.session = session
            self.config = config
            self.loop = asyncio.new_event_loop()
            self.agent = self
            self.plan = None
            self.approved_domains = ()
            self.plan_mode = plan
            self.show_reasoning = False
            self.permission = SimpleNamespace(allow_all=auto_approve, mode=Mode.BYPASS if auto_approve else Mode.STANDARD)
            self.pending_attachments = []
            self.on_event = lambda event: None
            self.cancelled = asyncio.Event()
            self.started = threading.Event()

        async def run_turn(self, text, attachments):
            self.session.record({"role": "user", "content": text})
            if text == "wait":
                self.started.set()
                await self.cancelled.wait()
                self.session.set_status("cancelled")
                return TurnResult("cancelled")
            self.on_event(ChatStreamEvent("text_delta", text="hello"))
            self.session.record({"role": "assistant", "content": "hello"})
            self.session.set_status("complete")
            return TurnResult("complete", text="hello")

        async def close_async(self):
            pass

        def cancel_turn(self):
            self.cancelled.set()

    monkeypatch.setattr(web, "build_state", lambda config, session, console, **kwargs: FakeState(session, **kwargs))
    server = web.WebServer(("127.0.0.1", 0), config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(path, body=None):
        data = None if body is None else json.dumps(body).encode()
        headers = {} if body is None else {"Content-Type": "application/json"}
        with urlopen(Request(base + path, data=data, headers=headers)) as response:
            return json.load(response)

    try:
        created = request("/api/sessions", {})
        sid = created["id"]
        assert created["workspace"] == str(workspace)
        assert request("/api/sessions")[0]["id"] == sid
        assert request(f"/api/sessions/{sid}/command", {"text": "/plan on"})["message"] == "plan mode: on"
        assert request(f"/api/sessions/{sid}")["planMode"] is True
        assert request("/api/sessions", {"from": sid})["planMode"] is True
        attachment = request(f"/api/sessions/{sid}/upload", {
            "name": "notes.txt", "mediaType": "text/plain", "data": "aGVsbG8=",
        })
        assert attachment["attachments"] == [{"name": "notes.txt", "size": 5}]
        with urlopen(Request(base + f"/api/sessions/{sid}/turn", data=b'{"text":"hi"}',
                             headers={"Content-Type": "application/json"})) as response:
            events = [json.loads(line) for line in response]
        assert [event["kind"] for event in events] == ["text_delta", "done"]
        assert events[-1]["status"] == "complete"
        assert request(f"/api/sessions/{sid}")["attachments"] == []
        assert request(f"/api/sessions/{sid}")["messages"] == [
            {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
        ]
        with ThreadPoolExecutor(max_workers=1) as pool:
            def wait_turn():
                with urlopen(Request(base + f"/api/sessions/{sid}/turn", data=b'{"text":"wait"}',
                                     headers={"Content-Type": "application/json"})) as response:
                    return [json.loads(line) for line in response]

            future = pool.submit(wait_turn)
            assert server.states[sid].started.wait(3)
            assert request(f"/api/sessions/{sid}/cancel", {})["message"] == "Cancellation requested"
            assert future.result(timeout=3)[-1]["status"] == "cancelled"
        fork = request(f"/api/sessions/{sid}/command", {"text": "/fork"})
        assert fork["sessionId"] != sid
        assert request(f"/api/sessions/{fork['sessionId']}")["messages"] == request(f"/api/sessions/{sid}")["messages"]
        other = LocalSession.new("fake", str(tmp_path / "other"))
        try:
            request(f"/api/sessions/{other.id}")
            raise AssertionError("other workspace session was exposed")
        except HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.close_states()
        server.server_close()
        thread.join()


def test_web_review_resumes_real_runtime(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions")

    class LocalSession:
        @staticmethod
        def new(model, workspace):
            return Session.new(model, workspace, store)

        @staticmethod
        def load(session_id):
            return Session.load(session_id, store)

        @staticmethod
        def list_all():
            return Session.list_all(store)

    monkeypatch.setattr(web, "Session", LocalSession)
    config = Config(api_key="unused", model="fake", workspace=tmp_path)

    class Provider:
        model = "fake"
        capabilities = ProviderCapabilities()

        def __init__(self):
            self.responses = [
                ChatResponse("", (ToolCall("write", "write_file", {"path": "created.txt", "content": "ok"}),)),
                ChatResponse("done"),
            ]

        async def stream(self, request):
            response = self.responses.pop(0)
            if response.content:
                yield ChatStreamEvent("text_delta", text=response.content)
            yield ChatStreamEvent("completed", response=response)

    def build(config, session, console, **kwargs):
        state = State(config, session, Console(quiet=True), LLM(config),
                      ReviewManager(tmp_path, store=GrantStore(tmp_path / "grants.json")), Hooks([], []),
                      mcp=MCPManager(tmp_path / "no-mcp.json"))
        state.rebuild(provider=Provider())
        return state

    monkeypatch.setattr(web, "build_state", build)
    server = web.WebServer(("127.0.0.1", 0), config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(path, body):
        with urlopen(Request(base + path, data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})) as response:
            return [json.loads(line) for line in response] if path.endswith(("/turn", "/review")) else json.load(response)

    try:
        sid = request("/api/sessions", {})["id"]
        events = request(f"/api/sessions/{sid}/turn", {"text": "Create the file"})
        assert events[-1]["status"] == "waiting"
        assert not (tmp_path / "created.txt").exists()
        review_id = events[-1]["review"]["id"]
        events = request(f"/api/sessions/{sid}/review", {"id": review_id, "decision": "approve"})
        assert events[-1]["status"] == "complete"
        assert (tmp_path / "created.txt").read_text() == "ok"
    finally:
        server.shutdown()
        server.close_states()
        server.server_close()
        thread.join()
