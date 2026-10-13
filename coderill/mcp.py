"""A distilled MCP stdio client.

Servers are configured in ``~/.coderill/mcp.json``:

    {"mcpServers": {"fs": {"command": "npx", "args": ["-y", "some-fs-server", "/tmp"]}}}

Each server is a subprocess speaking JSON-RPC 2.0, one message per line over
stdin/stdout. We complete the handshake, pull the tool list, and expose every
remote tool to the agent as ``mcp__<server>__<tool>`` so the loop treats them
exactly like the built-ins. A server that hangs or dies fails that one call as
an ordinary error string; it never kills the loop.
"""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import threading
from pathlib import Path

from .tools.base import Tool

CONFIG_FILE = Path.home() / ".coderill" / "mcp.json"
PROTOCOL_VERSION = "2024-11-05"

_CLIENTS: list[MCPClient] = []


class MCPError(RuntimeError):
    pass


class MCPClient:
    """One MCP server subprocess."""

    def __init__(self, name: str, command: str, args: list | None = None, env: dict | None = None) -> None:
        self.name = name
        self._id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._proc = subprocess.Popen(
            [command, *(args or [])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env={**os.environ, **(env or {})},
        )
        threading.Thread(target=self._read_loop, daemon=True).start()
        self._request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "coderill", "version": "0.1.0"}},
            15,
        )
        self._notify("notifications/initialized")

    def tools(self) -> list[dict]:
        return self._request("tools/list", {}, 15).get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        result = self._request("tools/call", {"name": tool_name, "arguments": arguments}, 120)
        return _render(result)

    def close(self) -> None:
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
        except OSError:
            pass

    # -- transport ---------------------------------------------------------

    def _send(self, message: dict) -> None:
        with self._lock:
            if self._proc.stdin is None or self._proc.poll() is not None:
                raise MCPError(f"MCP server '{self.name}' is not running")
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()

    def _notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _request(self, method: str, params: dict, timeout: float) -> dict:
        self._id += 1
        request_id = self._id
        box: dict = {"event": threading.Event()}
        self._pending[request_id] = box
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            if not box["event"].wait(timeout):
                raise MCPError(f"MCP server '{self.name}' timed out on {method}")
            if "error" in box:
                raise MCPError(f"MCP error on {method}: {box['error']}")
            return box.get("result", {})
        finally:
            self._pending.pop(request_id, None)

    def _read_loop(self) -> None:
        for line in self._proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            box = self._pending.get(message.get("id"))
            if box is None:
                continue
            if "error" in message:
                box["error"] = message["error"]
            else:
                box["result"] = message.get("result", {})
            box["event"].set()
        # The stream ended: fail anyone still waiting instead of hanging.
        for box in list(self._pending.values()):
            box["error"] = {"message": "server exited"}
            box["event"].set()


class MCPTool(Tool):
    """A remote MCP tool, exposed to the agent like a built-in."""

    def __init__(self, client: MCPClient, spec: dict) -> None:
        self.client = client
        self._remote = spec["name"]
        self.name = f"mcp__{client.name}__{spec['name']}"
        self.description = spec.get("description", "")
        self.parameters = spec.get("inputSchema") or {"type": "object", "properties": {}}
        self.read_only = False

    def execute(self, **kwargs) -> str:
        return self.client.call_tool(self._remote, kwargs)


def _render(result: dict) -> str:
    parts = []
    for item in result.get("content", []):
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
        else:
            parts.append(json.dumps(item))
    text = "\n".join(parts) if parts else json.dumps(result)
    return f"Error: {text}" if result.get("isError") else text


def load_mcp_tools(path: Path = CONFIG_FILE) -> list[Tool]:
    """Start every configured server and return its tools. Bad servers are skipped."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        config = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    tools: list[Tool] = []
    for name, spec in (config.get("mcpServers") or {}).items():
        try:
            client = MCPClient(name, spec["command"], spec.get("args", []), spec.get("env"))
            _CLIENTS.append(client)
            tools.extend(MCPTool(client, tool_spec) for tool_spec in client.tools())
        except Exception:  # noqa: BLE001, S112 - one bad server must not stop the rest
            continue
    return tools


@atexit.register
def _shutdown() -> None:
    for client in _CLIENTS:
        client.close()
