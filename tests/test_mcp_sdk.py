"""The optional MCP manager uses the SDK transport and session lifecycle."""

import asyncio
import json
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from coderill.mcp import MCPManager


def test_search_exposes_only_selected_mcp_schemas_and_restores_activation(tmp_path):
    from coderill.agent.runtime import AgentRuntime
    from coderill.config import Config
    from coderill.mcp import MCPTool
    from coderill.permissions import GrantStore, Mode, ReviewManager
    from coderill.providers.types import ChatResponse, ChatStreamEvent, ToolCall
    from coderill.session import Session, SessionStore
    from coderill.tools import build_registry

    manager = MCPManager(tmp_path / "absent.json")
    manager.tools = [MCPTool(manager, "files", SimpleNamespace(
        name="lookup", description="Search documents", inputSchema={"type": "object", "properties": {}})),
        MCPTool(manager, "calendar", SimpleNamespace(
            name="events", description="List meetings", inputSchema={"type": "object", "properties": {}}))]

    async def connect():
        return manager.tools

    manager.connect = connect

    class Provider:
        def __init__(self):
            self.requests = []
            self.responses = [ChatResponse("", (ToolCall("search", "mcp_tool_search", {"query": "documents"}),)),
                              ChatResponse("done")]

        async def stream(self, request):
            self.requests.append(request)
            yield ChatStreamEvent("completed", response=self.responses.pop(0))

    store = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), store)
    provider = Provider()
    runtime = AgentRuntime(Config(api_key="unused", workspace=tmp_path), session, build_registry(tmp_path),
                           provider, ReviewManager(tmp_path, mode=Mode.BYPASS,
                                                   store=GrantStore(tmp_path / "grants.json")), mcp=manager)
    assert asyncio.run(runtime.run_turn("Find a document")).status == "complete"
    first = {tool.name for tool in provider.requests[0].tools}
    second = {tool.name for tool in provider.requests[1].tools}
    assert "mcp_tool_search" in first
    assert "mcp__files__lookup" not in first
    assert "mcp__files__lookup" in second
    assert "mcp__calendar__events" not in second
    assert Session.load(session.id, store).active_mcp_tools == ["mcp__files__lookup"]


def test_stdio_and_http_tool_discovery(monkeypatch, tmp_path):
    pytest.importorskip("mcp")
    import mcp
    import mcp.client.stdio
    import mcp.client.streamable_http

    opened = []

    @asynccontextmanager
    async def transport(*args, **kwargs):
        opened.append(args[0])
        yield "read", "write"

    class Session:
        def __init__(self, read, write):
            self.initialized = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            self.initialized = True

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="echo", description="Echo", inputSchema={
                "type": "object", "properties": {"text": {"type": "string"}}
            })])

        async def call_tool(self, name, arguments):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=arguments["text"])], isError=False)

    monkeypatch.setattr(mcp, "ClientSession", Session)
    monkeypatch.setattr(mcp.client.stdio, "stdio_client", transport)
    monkeypatch.setattr(mcp.client.streamable_http, "streamable_http_client", transport)
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {
        "local": {"command": "fake", "args": []},
        "remote": {"url": "https://example.com/mcp"},
    }}))

    async def scenario():
        manager = MCPManager(path)
        tools = await manager.connect()
        assert {tool.name for tool in tools} == {"mcp__local__echo", "mcp__remote__echo"}
        assert await tools[0].aexecute(text="hello") == "hello"
        await manager.close()
        assert not manager.sessions

    asyncio.run(scenario())
    assert len(opened) == 2


def test_real_stdio_and_http_log_servers_can_disconnect_independently(tmp_path):
    pytest.importorskip("mcp.server.fastmcp")
    server = tmp_path / "log_server.py"
    log = tmp_path / "backend.log"
    log.write_text("backend started\n", encoding="utf-8")
    server.write_text('''import sys
from pathlib import Path
from mcp.server.fastmcp import FastMCP
log = Path(sys.argv[2])
app = FastMCP("backend-logs", host="127.0.0.1", port=int(sys.argv[3]), stateless_http=True)
@app.tool()
def read_log() -> str:
    return log.read_text(encoding="utf-8")
@app.tool()
def append_log(entry: str) -> str:
    with log.open("a", encoding="utf-8") as handle:
        handle.write(entry + "\\n")
    return "appended"
app.run("streamable-http" if sys.argv[1] == "http" else "stdio")
''', encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {
        "broken": {"command": str(tmp_path / "missing-server")},
        "stdio": {"command": sys.executable, "args": [str(server), "stdio", str(log), str(port)]},
        "http": {"url": f"http://127.0.0.1:{port}/mcp"},
    }}), encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(server), "http", str(log), str(port)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("HTTP MCP server did not start")

        async def scenario():
            from coderill.agent.runtime import AgentRuntime
            from coderill.config import Config
            from coderill.permissions import GrantStore, Mode, ReviewManager
            from coderill.providers.types import ChatResponse, ChatStreamEvent, ToolCall
            from coderill.session import Session, SessionStore
            from coderill.tools import build_registry

            manager = MCPManager(config)
            try:
                tools = {tool.name: tool for tool in await manager.connect()}
                assert set(manager.errors) == {"broken"} and len(tools) == 4
                assert "backend started" in await tools["mcp__stdio__read_log"].aexecute()
                assert await tools["mcp__http__append_log"].aexecute(entry="request complete") == "appended"

                class Provider:
                    def __init__(self):
                        self.responses = [
                            ChatResponse("", (ToolCall("search", "mcp_tool_search", {"query": "read_log"}),)),
                            ChatResponse("", (ToolCall("read", "mcp__http__read_log", {}),)),
                            ChatResponse("done"),
                        ]

                    async def stream(self, _request):
                        yield ChatStreamEvent("completed", response=self.responses.pop(0))

                session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
                runtime = AgentRuntime(Config(api_key="unused", workspace=tmp_path), session,
                                       build_registry(tmp_path), Provider(),
                                       ReviewManager(tmp_path, mode=Mode.BYPASS,
                                                     store=GrantStore(tmp_path / "grants.json")), mcp=manager)
                assert (await runtime.run_turn("Read the backend log")).status == "complete"
                assert "request complete" in session.finished_calls["read"]["content"]
                await manager.disconnect("stdio")
                assert "request complete" in await tools["mcp__http__read_log"].aexecute()
                assert {tool.server for tool in manager.tools} == {"http"}
            finally:
                await manager.close()

        asyncio.run(scenario())
    finally:
        process.terminate()
        process.wait(timeout=5)
