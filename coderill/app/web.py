"""Local browser client over the same State and AgentRuntime as the CLI."""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from rich.console import Console

from .. import __version__
from ..config import Config
from ..providers.openai_compatible import session_cost
from ..session import Session
from .cli import HELP_TEXT, _handle_command
from .factory import build_state
from .state import State

# shortcut: Web assets live in the source checkout; bundle them into the wheel when publishing the client.
DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def session_data(session: Session, state: State) -> dict:
    known_cost, complete_cost = session_cost(session.usage_by_model)
    if state.config.routing.get("embedding") and any(
            item.get("stage") in ("mcp_search", "skill_search") for item in session.routing_events):
        complete_cost = False
    children = []
    for child in session.subagents.values():
        item = dict(child)
        try:
            child_session = Session.load(child["id"], session.store)
            item["usage"] = child_session.usage
        except (OSError, ValueError, KeyError):
            item["usage"] = None
        children.append(item)
    return {
        "id": session.id, "model": state.config.model, "provider": state.config.provider,
        "workspace": session.workspace, "updated": session.updated, "status": session.last_status,
        "review": session.pending_review, "plan": state.agent.plan.describe() if state.agent.plan else "",
        "planMode": state.plan_mode, "autoApprove": state.permission.allow_all,
        "mode": state.permission.mode.value, "reasoning": state.show_reasoning,
        "sandbox": {"backend": state.config.sandbox, "profile": state.config.sandbox_profile,
                    "network": state.config.sandbox_network,
                    "approvedDomains": list(state.agent.approved_domains),
                    "outside": ["hooks", "MCP", "browser", "computer_use", "fetch", "web_search"]},
        "profiles": [
            {"name": name, "model": profile.get("model", ""), "provider": profile.get("provider", "")}
            for name, profile in state.config.profiles.items()
        ],
        "attachments": [{"name": item["name"], "size": item["size"]} for item in state.pending_attachments],
        "usage": {**session.usage, "cost": known_cost if complete_cost else None,
                  "known_cost": known_cost, "by_model": session.usage_by_model},
        "background": list(session.background_tasks.values()), "children": children,
        "messages": [
            {"role": item["role"], "content": item["content"][:1200] if item["role"] == "tool" else item["content"]}
            for item in session.messages
            if item.get("role") in ("user", "assistant", "tool")
            and isinstance(item.get("content"), str) and item.get("content") and not item.get("synthetic")
        ],
    }


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], config: Config, *, plan: bool = False,
                 auto_approve: bool = False):
        super().__init__(address, WebHandler)
        self.config = config
        self.plan = plan
        self.auto_approve = auto_approve
        self.states: dict[str, State] = {}
        self.locks: dict[str, threading.Lock] = {}
        self.reviewed_diffs: dict[tuple[str, str], str] = {}
        self._guard = threading.Lock()

    def state(self, session: Session, source: State | None = None) -> State:
        with self._guard:
            if session.id not in self.states:
                config = source.config if source else self.config
                state = build_state(config, session, Console(quiet=True),
                                    auto_approve=source.permission.allow_all if source else self.auto_approve,
                                    plan=source.plan_mode if source else self.plan)
                if source:
                    state.permission.mode = source.permission.mode
                    state.show_reasoning = source.show_reasoning
                threading.Thread(target=self._run_loop, args=(state.loop,), daemon=True).start()
                self.states[session.id] = state
                self.locks[session.id] = threading.Lock()
            return self.states[session.id]

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def close_states(self) -> None:
        for state in self.states.values():
            asyncio.run_coroutine_threadsafe(state.close_async(), state.loop).result()
            state.loop.call_soon_threadsafe(state.loop.stop)


class WebHandler(BaseHTTPRequestHandler):
    server: WebServer

    def _local_request(self) -> bool:
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        allowed_hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
        return host in allowed_hosts and (
            origin is None or origin in {f"http://{name}:{port}" for name in ("127.0.0.1", "localhost")
                                         for port in (self.server.server_port, 5173)}
        )

    def _json(self, status: int, data: object) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _session(self, session_id: str) -> Session:
        session = Session.load(session_id)
        if session.workspace != str(self.server.config.workspace):
            raise FileNotFoundError(session_id)
        return session

    @staticmethod
    def _run(state: State, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, state.loop).result()

    def do_GET(self) -> None:
        if not self._local_request():
            self._json(403, {"error": "Local requests only"})
            return
        path = urlsplit(self.path).path
        if path == "/healthz":
            ready = (DIST / "index.html").is_file() and self.server.config.workspace.is_dir()
            self._json(200 if ready else 503, {"status": "ok" if ready else "not_ready", "version": __version__})
            return
        if path == "/api/sessions":
            self._json(200, [
                {"id": item.id, "model": item.model, "updated": item.updated,
                 "title": next((str(message.get("content", ""))[:48] for message in item.messages
                                if message.get("role") == "user" and not message.get("synthetic")), "新建会话")}
                for item in Session.list_all() if item.workspace == str(self.server.config.workspace)
            ])
            return
        if path == "/api/meta":
            self._json(200, {"version": __version__, "workspace": str(self.server.config.workspace), "help": HELP_TEXT})
            return
        if path.startswith("/api/sessions/"):
            try:
                session = self._session(path.removeprefix("/api/sessions/"))
                self._json(200, session_data(session, self.server.state(session)))
            except (FileNotFoundError, ValueError):
                self._json(404, {"error": "Session not found"})
            return
        target = DIST / (path.lstrip("/") or "index.html")
        if not target.resolve().is_relative_to(DIST.resolve()) or not target.is_file():
            self.send_error(404, "Build the web app with npm run build first")
            return
        content_type = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(
            target.suffix, "application/octet-stream",
        )
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not self._local_request():
            self._json(403, {"error": "Local requests only"})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "Expected JSON"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 15_000_000:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise TypeError("Expected an object")
            path = urlsplit(self.path).path
            if path == "/api/sessions":
                source_id = payload.get("from")
                source = self.server.state(self._session(source_id)) if isinstance(source_id, str) else None
                config = source.config if source else self.server.config
                session = Session.new(config.model, str(config.workspace))
                self._json(201, session_data(session, self.server.state(session, source)))
                return
            parts = path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "sessions"]:
                self._json(404, {"error": "Not found"})
                return
            session = self._session(parts[2])
            state = self.server.state(session)
            action = parts[3]
            if action == "cancel":
                if state.loop.is_running():
                    state.loop.call_soon_threadsafe(state.agent.cancel_turn)
                self._json(200, {"message": "Cancellation requested"})
                return
            lock = self.server.locks[session.id]
            if not lock.acquire(blocking=False):
                self._json(409, {"error": "This session is busy"})
                return
            try:
                if action in ("turn", "review", "recover"):
                    self._stream(state, action, payload)
                elif action == "command":
                    self._json(200, self._run(state, self._command(state, payload)))
                elif action == "upload":
                    self._json(200, self._run(state, self._upload(state, payload)))
                elif action == "apply":
                    self._json(200, self._run(state, self._apply(state, payload)))
                else:
                    self._json(404, {"error": "Not found"})
            finally:
                lock.release()
        except FileNotFoundError:
            self._json(404, {"error": "Session not found"})
        except (TypeError, ValueError, KeyError, RuntimeError, binascii.Error) as exc:
            self._json(400, {"error": str(exc)})

    def _stream(self, state: State, action: str, payload: dict) -> None:
        if action == "turn":
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 100_000:
                raise ValueError("Message must be 1–100000 characters")
            if state.session.pending_review:
                raise ValueError("Resolve the pending review first")
            if state.session.incomplete_calls or state.session.last_status == "running":
                raise ValueError("Resume the interrupted turn first")
        elif action == "review":
            if payload.get("id") != (state.session.pending_review or {}).get("id") or payload.get("decision") not in (
                "approve", "always", "deny",
            ):
                raise ValueError("Current review ID and decision required")
        elif not (state.session.incomplete_calls or state.session.last_status == "running"):
            raise ValueError("No interrupted turn to resume")

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        disconnected = False

        def emit(data: dict) -> None:
            nonlocal disconnected
            if disconnected:
                return
            try:
                self.wfile.write((json.dumps(data, ensure_ascii=False) + "\n").encode())
                self.wfile.flush()
            except OSError:
                disconnected = True
                state.agent.cancel_turn()

        def on_event(event) -> None:
            if event.kind in ("text_delta", "reasoning_delta", "tool_started", "tool_result", "status") and (
                event.kind != "reasoning_delta" or state.show_reasoning
            ):
                emit({"kind": event.kind, "text": event.text,
                      "tool": event.tool_call.name if event.tool_call else "", "status": event.status})

        state.agent.on_event = on_event
        try:
            if action == "turn":
                result = self._run(state, state.agent.run_turn(text.strip(), state.pending_attachments))
                if result.status != "failed":
                    state.pending_attachments.clear()
            elif action == "review":
                result = self._run(state, state.agent.resume_turn(payload["id"], payload["decision"]))
            else:
                result = self._run(state, state.agent.resume_turn())
            emit({"kind": "done", "status": result.status, "text": result.text,
                  "error": result.error, "review": result.review})
        except Exception as exc:  # noqa: BLE001 - stream errors must terminate with a readable result
            emit({"kind": "done", "status": "failed", "error": str(exc)})

    async def _command(self, state: State, payload: dict) -> dict:
        text = payload.get("text")
        if not isinstance(text, str) or not text.startswith("/") or len(text) > 4096:
            raise ValueError("Expected a slash command")
        command, _, rest = text.partition(" ")
        rest = rest.strip()
        session = state.session
        if command in ("/exit", "/quit"):
            return {"message": "已退出当前会话", "exit": True}
        if command == "/new":
            new = Session.new(state.config.model, str(state.config.workspace))
            self.server.state(new, state)
            return {"message": f"已新建会话 {new.id}", "sessionId": new.id}
        if command == "/list":
            return {"message": "\n".join(item.id for item in Session.list_all()
                                         if item.workspace == str(self.server.config.workspace)) or "暂无会话"}
        if command == "/resume":
            matches = [item for item in Session.list_all() if item.id.startswith(rest)
                       and item.workspace == str(self.server.config.workspace)] if rest else []
            if len(matches) != 1:
                raise ValueError("请输入唯一的会话 ID 或前缀")
            return {"message": f"已打开 {matches[0].id}", "sessionId": matches[0].id}
        if command == "/fork":
            new = session.fork()
            self.server.state(new, state)
            return {"message": f"已分叉会话 {new.id}", "sessionId": new.id}
        if command == "/mcp":
            await state.mcp.connect()
            return {"message": "\n".join(f"{tool.name} — {tool.description}" for tool in state.mcp_tools)
                    or "未加载 MCP 工具（检查 ~/.coderill/mcp.json）"}
        if command == "/background":
            service = state.subagent_services[session.id]
            notices = service.background.notifications()
            lines = [f"{item['id']} {item['status']}: {item['result']}" for item in notices]
            lines.extend(f"{item['id']} {item['status']}: {item['description']}"
                         for item in session.background_tasks.values())
            return {"message": "\n".join(lines) or "暂无后台任务"}
        if command == "/apply":
            if not rest:
                raise ValueError("请提供子任务 ID")
            diff = await state.subagent_services[session.id].preview_apply(rest)
            self.server.reviewed_diffs[(session.id, rest)] = diff
            return {"message": "没有待应用的修改" if not diff else "请检查差异后再应用",
                    "applyPreview": {"childId": rest, "diff": diff} if diff else None}
        if command not in {"/help", "/where", "/cost", "/share", "/undo", "/plan", "/approve",
                           "/mode", "/reasoning", "/attach", "/model", "/memory"}:
            raise ValueError(f"未知命令：{command}")
        output = io.StringIO()
        console = Console(file=output, force_terminal=False, color_system=None, width=120)
        _handle_command(state, console, text)
        return {"message": output.getvalue().strip()}

    async def _upload(self, state: State, payload: dict) -> dict:
        name, media_type, encoded = (payload.get(key) for key in ("name", "mediaType", "data"))
        if not isinstance(name, str) or not name or len(name) > 255 or Path(name).name != name:
            raise ValueError("Invalid attachment name")
        if not isinstance(media_type, str) or not isinstance(encoded, str) or len(encoded) > 14_000_000:
            raise ValueError("Invalid attachment")
        data = base64.b64decode(encoded, validate=True)
        item = state.session.attach_bytes(name, data, media_type)
        state.pending_attachments.append(item)
        return {"message": f"已添加 {name}", "attachments": session_data(state.session, state)["attachments"]}

    async def _apply(self, state: State, payload: dict) -> dict:
        child_id = payload.get("childId")
        if not isinstance(child_id, str):
            raise TypeError("Invalid child ID")
        key = (state.session.id, child_id)
        if key not in self.server.reviewed_diffs:
            raise ValueError("请先预览子任务差异")
        diff = self.server.reviewed_diffs.pop(key)
        return {"message": await state.subagent_services[state.session.id].apply(child_id, diff)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run CodeRill's local web interface")
    parser.add_argument("--cwd", default=None, help="Workspace directory")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1",
                        help="Bind address; use 0.0.0.0 inside a container, publish its port on host loopback only")
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", choices=("openai", "anthropic"))
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--sandbox", choices=("off", "docker"))
    parser.add_argument("--sandbox-profile", choices=("read-only", "workspace-write"))
    parser.add_argument("--sandbox-network", choices=("restricted", "none"))
    parser.add_argument("--sandbox-image")
    parser.add_argument("--sandbox-port", action="append", default=[])
    parser.add_argument("--version", action="version", version=f"coderill {__version__}")
    args = parser.parse_args(argv)
    config = Config.from_env(workspace=args.cwd, overrides={"model": args.model, "provider": args.provider,
                                                        "benchmark": args.benchmark, "sandbox": args.sandbox,
                                                        "sandbox_profile": args.sandbox_profile,
                                                        "sandbox_network": args.sandbox_network,
                                                        "sandbox_image": args.sandbox_image,
                                                        "sandbox_ports": args.sandbox_port or None})
    with WebServer((args.host, args.port), config, plan=args.plan,
                   auto_approve=args.auto_approve) as server:
        print(f"CodeRill web: http://127.0.0.1:{args.port}  workspace: {config.workspace}")
        try:
            server.serve_forever()
        finally:
            server.close_states()


if __name__ == "__main__":
    main()
