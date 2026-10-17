"""MCP stdio and Streamable HTTP connections through the official SDK."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, ClassVar

from .tools.base import Tool, ToolError

CONFIG_FILE = Path.home() / ".coderill" / "mcp.json"


class MCPTool(Tool):
    def __init__(self, manager: MCPManager, server: str, spec: Any) -> None:
        self.manager = manager
        self.server = server
        self.remote_name = spec.name
        self.name = f"mcp__{server}__{spec.name}"
        self.description = spec.description or ""
        self.parameters = spec.inputSchema or {"type": "object", "properties": {}}

    async def aexecute(self, **kwargs: Any) -> str:
        return await self.manager.call_tool(self.server, self.remote_name, kwargs)

    def execute(self, **kwargs: Any) -> str:
        raise ToolError("MCP calls require the async runtime")


class MCPToolSearch(Tool):
    name = "mcp_tool_search"
    description = "Find connected MCP capabilities by name or description; matching tools become available this turn."
    read_only = True
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object", "properties": {"query": {"type": "string"}},
        "required": ["query"], "additionalProperties": False,
    }

    def __init__(self, manager: MCPManager) -> None:
        self.manager = manager

    def execute(self, query: str) -> str:
        words = set(re.findall(r"\w+", query.casefold()))
        if not words:
            raise ToolError("search query must not be blank")
        ranked = []
        for tool in self.manager.tools:
            name = tool.name.casefold()
            description = tool.description.casefold()
            score = sum(10 if word in name else 1 if word in description else 0 for word in words)
            if score:
                ranked.append((-score, name, tool))
        ranked.sort()
        return json.dumps({"matches": [
            {"name": tool.name, "description": tool.description[:240]}
            for _, _, tool in ranked[:8]
        ]}, ensure_ascii=False)


class MCPManager:
    def __init__(self, path: Path = CONFIG_FILE) -> None:
        self.path = Path(path)
        self.tools: list[MCPTool] = []
        self.sessions: dict[str, Any] = {}
        self._connections: dict[str, tuple[asyncio.Task[None], asyncio.Event]] = {}
        self.errors: dict[str, str] = {}

    async def connect(self) -> list[MCPTool]:
        if not self.path.exists():
            return []
        config = json.loads(self.path.read_text(encoding="utf-8"))
        for name, spec in (config.get("mcpServers") or {}).items():
            if name in self.sessions:
                continue
            try:
                await self._connect_one(name, spec)
                self.errors.pop(name, None)
            except Exception as exc:  # noqa: BLE001 - one server must not block other tools
                self.errors[name] = str(exc)
        return self.tools

    async def _connect_one(self, name: str, spec: dict[str, Any]) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client

        ready: asyncio.Future[tuple[Any, list[Any]]] = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()

        async def serve() -> None:
            try:
                async with AsyncExitStack() as stack:
                    if "url" in spec:
                        streams = await stack.enter_async_context(streamable_http_client(spec["url"]))
                    else:
                        params = StdioServerParameters(
                            command=spec["command"], args=spec.get("args", []), env=spec.get("env"),
                        )
                        streams = await stack.enter_async_context(stdio_client(params))
                    session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
                    await session.initialize()
                    discovered = await session.list_tools()
                    ready.set_result((session, discovered.tools))
                    await stop.wait()
            except BaseException as exc:
                if not ready.done():
                    ready.set_exception(exc)
                else:
                    raise

        task = asyncio.create_task(serve())
        try:
            session, specs = await ready
        except BaseException:
            stop.set()
            await asyncio.gather(task, return_exceptions=True)
            raise
        self.sessions[name] = session
        self._connections[name] = (task, stop)
        self.tools.extend(MCPTool(self, name, tool) for tool in specs)

    async def call_tool(self, server: str, name: str, arguments: dict[str, Any]) -> str:
        session = self.sessions.get(server)
        if session is None:
            raise ToolError(f"MCP server '{server}' is disconnected")
        try:
            result = await session.call_tool(name, arguments)
        except Exception as exc:
            await self.disconnect(server)
            raise ToolError(f"MCP server '{server}' disconnected: {exc}") from exc
        content = []
        for item in result.content:
            if getattr(item, "type", "") == "text":
                content.append(item.text)
            else:
                content.append(item.model_dump_json())
        body = "\n".join(content)
        return f"Error: {body}" if result.isError else body

    async def disconnect(self, server: str) -> None:
        self.sessions.pop(server, None)
        self.tools = [tool for tool in self.tools if tool.server != server]
        connection = self._connections.pop(server, None)
        if connection is not None:
            task, stop = connection
            stop.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            if isinstance(result, BaseException):
                self.errors[server] = str(result)

    async def close(self) -> None:
        for server in list(self.sessions):
            await self.disconnect(server)
