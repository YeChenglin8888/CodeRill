"""Bounded public-web reads and search."""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import socket
from typing import ClassVar
from urllib import parse

from .base import Tool, ToolError

SEARCH_URL = "https://search.parallel.ai/mcp"


def _public_url(url: str) -> tuple[parse.SplitResult, str, int]:
    parsed = parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ToolError("URL must be a public http or https address without credentials")
    if parsed.hostname.lower().rstrip(".") in ("localhost", "ip6-localhost") or parsed.hostname.lower().endswith(".localhost"):
        raise ToolError("local, private, and reserved network addresses are not allowed")
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ToolError("local, private, and reserved network addresses are not allowed")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ToolError("invalid URL port") from exc
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port)
    except OSError as exc:
        raise ToolError(f"cannot resolve URL host: {exc}") from exc
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ToolError("local, private, and reserved network addresses are not allowed")
    return parsed, addresses[0][4][0], port


def _request_public(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
                    max_bytes: int = 100_000, timeout: int = 20) -> tuple[str, bytes]:
    parsed, address, port = _public_url(url)
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parsed.hostname, port, timeout=timeout)
    # Pin the validated IP while keeping the original host for HTTP Host and TLS certificate checks.
    connection._create_connection = lambda _target, wait, source_address=None: socket.create_connection(
        (address, port), wait, source_address=source_address)
    path = parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    try:
        connection.request("POST" if data is not None else "GET", path, body=data, headers=headers or {})
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise ToolError("redirects are not followed; inspect the new URL separately")
        if response.status >= 400:
            raise ToolError(f"HTTP {response.status}")
        return response.getheader("Content-Type", "text/plain"), response.read(max_bytes + 1)
    except OSError as exc:
        raise ToolError(f"network request failed: {exc}") from exc
    finally:
        connection.close()


class FetchTool(Tool):
    name = "fetch"
    description = "Read bounded text from one public http or https URL."
    parameters: ClassVar[dict] = {"type": "object", "properties": {
        "url": {"type": "string"}, "max_chars": {"type": "integer"},
    }, "required": ["url"]}

    def execute(self, url: str, max_chars: int = 20_000) -> str:
        if not 1 <= max_chars <= 100_000:
            raise ToolError("max_chars must be between 1 and 100000")
        media_type, body = _request_public(url, headers={"User-Agent": "CodeRill/0.1"}, max_bytes=max_chars * 4)
        media_type = media_type.split(";", 1)[0].lower()
        if not (media_type.startswith("text/") or media_type in ("application/json", "application/xml")):
            raise ToolError(f"unsupported response type: {media_type}")
        text = body.decode("utf-8", errors="replace")
        return text[:max_chars] + ("\n[response truncated]" if len(text) > max_chars else "")


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the public web and return result text with source links."
    parameters: ClassVar[dict] = {"type": "object", "properties": {
        "query": {"type": "string"}, "num_results": {"type": "integer"},
    }, "required": ["query"]}

    def execute(self, query: str, num_results: int = 8) -> str:
        if not query.strip() or not 1 <= num_results <= 20:
            raise ToolError("query is required and num_results must be between 1 and 20")
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "web_search", "arguments": {"query": query, "search_queries": [query],
                                                "objective": query, "num_results": num_results},
        }}).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "CodeRill/0.1"}
        if os.getenv("PARALLEL_API_KEY"):
            headers["Authorization"] = f"Bearer {os.environ['PARALLEL_API_KEY']}"
        _, raw = _request_public(SEARCH_URL, data=body, headers=headers, timeout=25)
        payload = raw.decode("utf-8", errors="replace")
        for line in [payload, *(item[6:] for item in payload.splitlines() if item.startswith("data: "))]:
            try:
                result = json.loads(line).get("result", {})
            except (AttributeError, json.JSONDecodeError):
                continue
            for item in result.get("content", []):
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    return item["text"][:30_000]
        raise ToolError("web search returned no readable results")
