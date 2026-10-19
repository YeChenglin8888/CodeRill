"""Optional Textual view over the same AgentRuntime used by the CLI."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult, ScreenStackError
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import Key
from textual.widgets import Button, Footer, Header, RichLog, Select, Static, TextArea

from ..agent.runtime import TurnResult
from ..language import is_chinese
from ..providers.types import ChatStreamEvent
from ..session import Session
from .clipboard import read_image
from .state import State


class Composer(TextArea):
    def on_key(self, event: Key) -> None:
        if event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.app._submit_message()


class CodeRillApp(App):
    TITLE = "≋ CodeRill"
    BINDINGS = (("ctrl+c", "cancel_turn", "Cancel"), ("ctrl+n", "new_session", "New"),
                ("f8", "paste_image", "Paste image"), ("ctrl+q", "quit", "Quit"))
    CSS = """
    #sidebar { width: 32; border-right: solid $accent; padding: 1; }
    #main { width: 1fr; padding: 1; }
    #history { height: 1fr; border: round $accent; }
    #plan { height: 1fr; overflow-y: auto; }
    #approval { display: none; height: auto; max-height: 12; overflow-y: auto; border: round yellow; }
    #approval-actions { display: none; height: auto; }
    #message { height: 3; min-height: 3; max-height: 10; border: round #789183; padding: 0 1; }
    #message:focus { border: round #5e806d; }
    #attachments { height: 1; }
    """

    def __init__(self, state: State) -> None:
        super().__init__()
        self.state = state
        self.active_task: asyncio.Task | None = None
        self.review: dict | None = None
        self.streaming_text = ""
        self.state.agent.on_event = self._runtime_event

    def compose(self) -> ComposeResult:
        zh = is_chinese(self.state.session.messages)
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static("会话" if zh else "Sessions", id="sessions-label")
                yield Select([(self.state.session.id, self.state.session.id)], id="sessions", allow_blank=False)
                yield Static("模型" if zh else "Models", id="models-label")
                yield Select([(self.state.config.model, "current")], id="models", allow_blank=False)
                yield Static("任务计划" if zh else "Task plan", id="plan-label", classes="heading")
                yield Static("", id="plan", markup=False)
            with Vertical(id="main"):
                yield RichLog(id="history", wrap=True, highlight=True, markup=False)
                yield Static("", id="stream", markup=False)
                yield Static("", id="approval", markup=False)
                with Horizontal(id="approval-actions"):
                    yield Button("仅批准本次" if zh else "Approve once", id="approve", variant="success")
                    yield Button("始终允许同类操作" if zh else "Always allow similar", id="always")
                    yield Button("拒绝" if zh else "Deny", id="deny", variant="error")
                yield Static("", id="attachments", markup=False)
                yield Composer(placeholder=("输入消息 · Enter 发送 · Shift+Enter 换行 · /attach 路径" if zh else
                                            "Message · Enter sends · Shift+Enter adds line · /attach path"), id="message")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_selectors()
        self._show_history()
        self._refresh_plan()
        if self.state.session.pending_review:
            self._show_result(TurnResult("waiting", review=self.state.session.pending_review))
        self.query_one("#message", Composer).focus()

    async def on_unmount(self) -> None:
        await self.state.close_async()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "message":
            event.text_area.styles.height = min(10, max(1, event.text_area.virtual_size.height) + 2)

    def _refresh_selectors(self) -> None:
        sessions = self.query_one("#sessions", Select)
        sessions.set_options([(f"{item.id} · {item.model} · {len(item.messages)}", item.id)
                              for item in Session.list_all(self.state.session.store)])
        sessions.value = self.state.session.id
        models = self.query_one("#models", Select)
        options = [(f"{self.state.config.model} (current)", "current")]
        options.extend(((f"{name} · {profile.get('provider', self.state.config.provider)}/"
                         f"{profile.get('model', self.state.config.model)}"), name)
                       for name, profile in self.state.config.profiles.items())
        models.set_options(options)
        models.value = "current"
        self.query_one("#attachments", Static).update(
            "Attached: " + ", ".join(item["name"] for item in self.state.pending_attachments)
            if self.state.pending_attachments else ""
        )

    def _show_history(self) -> None:
        log = self.query_one("#history", RichLog)
        log.clear()
        for message in self.state.session.messages:
            role = message.get("role", "?")
            if role in ("user", "assistant") and message.get("content"):
                log.write(f"[{role}] {message['content']}")
            elif role == "tool":
                log.write(f"[tool] {str(message.get('content', ''))[:500]}")

    def _refresh_plan(self) -> None:
        plan = self.state.agent.plan
        self.query_one("#plan", Static).update(plan.describe() if plan else "No plan")

    def _runtime_event(self, event: ChatStreamEvent) -> None:
        log = self.query_one("#history", RichLog)
        if event.kind == "text_delta":
            self.streaming_text += event.text
            self.query_one("#stream", Static).update(f"[assistant] {self.streaming_text}")
        elif event.kind == "tool_started" and event.tool_call:
            log.write(f"\n[tool] {event.tool_call.name} {event.tool_call.arguments}")
        elif event.kind == "tool_result":
            log.write(f"[result] {event.text[:500]}")

    def _submit_message(self) -> None:
        composer = self.query_one("#message", Composer)
        text = composer.text.strip()
        composer.clear()
        if not text or self.active_task is not None:
            return
        if self.review is not None:
            self.query_one("#history", RichLog).write(
                "[review] 请先处理待批准的操作。" if is_chinese(self.state.session.messages) else
                "[review] Resolve the pending approval before sending a message.")
            return
        if text.startswith("/attach "):
            try:
                item = self.state.session.attach_file(Path(text[8:].strip()))
                self.state.pending_attachments.append(item)
                self.query_one("#history", RichLog).write(f"[attached] {item['name']}")
                self._refresh_selectors()
            except (OSError, ValueError) as exc:
                self.query_one("#history", RichLog).write(f"[error] {exc}")
            return
        path = Path(text).expanduser()
        if "\n" not in text and path.is_file():
            try:
                item = self.state.session.attach_file(path)
                self.state.pending_attachments.append(item)
                self.query_one("#history", RichLog).write(f"[attached] {item['name']}")
                self._refresh_selectors()
            except (OSError, ValueError) as exc:
                self.query_one("#history", RichLog).write(f"[error] {exc}")
            return
        self.query_one("#history", RichLog).write(f"[user] {text}")
        self._set_language(any("\u4e00" <= char <= "\u9fff" for char in text))
        self.active_task = asyncio.create_task(self._run(text))

    def _set_language(self, zh: bool) -> None:
        for widget_id, chinese, english in (
            ("#sessions-label", "会话", "Sessions"),
            ("#models-label", "模型", "Models"),
            ("#plan-label", "任务计划", "Task plan"),
        ):
            self.query_one(widget_id, Static).update(chinese if zh else english)
        for widget_id, chinese, english in (
            ("#approve", "仅批准本次", "Approve once"),
            ("#always", "始终允许同类操作", "Always allow similar"),
            ("#deny", "拒绝", "Deny"),
        ):
            self.query_one(widget_id, Button).label = chinese if zh else english
        self.query_one("#message", Composer).placeholder = (
            "输入消息 · Enter 发送 · Shift+Enter 换行 · /attach 路径" if zh else
            "Message · Enter sends · Shift+Enter adds line · /attach path")

    async def _run(self, text: str) -> None:
        try:
            if self.state.session.incomplete_calls or self.state.session.last_status == "running":
                recovered = await self.state.agent.resume_turn()
                self._show_result(recovered)
                if recovered.status != "complete":
                    return
            result = await self.state.agent.run_turn(text, self.state.pending_attachments)
            if result.status != "failed":
                self.state.pending_attachments.clear()
                self._refresh_selectors()
            self._show_result(result)
        except asyncio.CancelledError:
            self.query_one("#history", RichLog).write("[cancelled]")
            self.query_one("#stream", Static).update("")
            raise
        finally:
            self.active_task = None

    def _show_result(self, result) -> None:
        log = self.query_one("#history", RichLog)
        if self.streaming_text:
            log.write(f"[assistant] {self.streaming_text}")
            self.streaming_text = ""
            self.query_one("#stream", Static).update("")
        if result.status == "waiting":
            self.review = result.review
            call = self.review["call"]
            zh = is_chinese(self.state.session.messages)
            self._set_language(zh)
            self.query_one("#approval", Static).update(
                f"{'请确认是否执行' if zh else 'Review'} {call['name']} {call['arguments']}\n"
                f"{self.review['diff']}\n{result.error or ''}"
            )
            self.query_one("#approval", Static).display = True
            self.query_one("#approval-actions", Horizontal).display = True
        else:
            self.review = None
            self.query_one("#approval", Static).display = False
            self.query_one("#approval-actions", Horizontal).display = False
            if result.status != "complete":
                log.write(f"\n[{result.status}] {result.error}")
            self._refresh_plan()
            self._refresh_selectors()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self.review is None or self.active_task is not None:
            return
        self.active_task = asyncio.create_task(self._resume(event.button.id))

    async def _resume(self, decision: str) -> None:
        try:
            result = await self.state.agent.resume_turn(self.review["id"], decision)
            self._show_result(result)
        finally:
            self.active_task = None

    def on_select_changed(self, event: Select.Changed) -> None:
        try:
            self.query_one("#history", RichLog)
        except (NoMatches, ScreenStackError):
            return
        if self.active_task is not None or event.value is Select.BLANK or event.value != event.select.value:
            return
        if event.select.id == "sessions" and event.value != self.state.session.id:
            self.state.session = Session.load(str(event.value), self.state.session.store)
            self.state.pending_attachments.clear()
            self.state.rebuild()
            self.state.agent.on_event = self._runtime_event
            self._show_history()
            self._refresh_plan()
            if self.state.session.pending_review:
                self._show_result(TurnResult("waiting", review=self.state.session.pending_review))
            else:
                self._show_result(TurnResult("complete"))
        elif event.select.id == "models" and event.value != "current":
            try:
                self.state.select_model(str(event.value))
                self.state.agent.on_event = self._runtime_event
                self._refresh_selectors()
            except (RuntimeError, ValueError) as exc:
                self.query_one("#history", RichLog).write(f"[error] {exc}")
                self._refresh_selectors()

    def action_cancel_turn(self) -> None:
        self.state.agent.cancel_turn()
        if self.active_task is not None:
            self.active_task.cancel()

    def action_new_session(self) -> None:
        if self.active_task is not None:
            return
        self.state.session = Session.new(self.state.config.model, str(self.state.config.workspace), self.state.session.store)
        self.state.pending_attachments.clear()
        self.state.rebuild()
        self.state.agent.on_event = self._runtime_event
        self._refresh_selectors()
        self._show_history()
        self._refresh_plan()

    def action_paste_image(self) -> None:
        data = read_image()
        if data is None:
            self.query_one("#history", RichLog).write("[clipboard] No PNG image available")
            return
        try:
            item = self.state.session.attach_bytes("clipboard.png", data, "image/png")
            self.state.pending_attachments.append(item)
            self._refresh_selectors()
        except ValueError as exc:
            self.query_one("#history", RichLog).write(f"[error] {exc}")


def run_tui(state: State) -> int:
    CodeRillApp(state).run()
    return 0
