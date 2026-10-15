import asyncio

from coderill.agent.runtime import TurnResult
from coderill.app.tui import CodeRillApp
from coderill.config import Config
from coderill.providers.types import ChatStreamEvent
from coderill.session import Session, SessionStore
from coderill.taskplan import TaskPlan


class FakeAgent:
    def __init__(self):
        self.on_event = lambda event: None
        self.plan = TaskPlan()

    async def run_turn(self, text, attachments):
        self.on_event(ChatStreamEvent("text_delta", text="thinking"))
        return TurnResult("waiting", review={
            "id": "r1", "call": {"name": "write_file", "arguments": {"path": "a"}}, "diff": "+new",
        })

    async def resume_turn(self, request_id, decision):
        self.on_event(ChatStreamEvent("text_delta", text="done"))
        return TurnResult("complete", text="done")

    def cancel_turn(self):
        pass


class FakeState:
    def __init__(self, tmp_path):
        self.config = Config(api_key="unused", workspace=tmp_path)
        self.session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
        self.agent = FakeAgent()
        self.pending_attachments = []
    async def close_async(self):
        pass

    def rebuild(self):
        self.agent = FakeAgent()


def test_tui_stream_review_and_approval(tmp_path):
    async def scenario():
        app = CodeRillApp(FakeState(tmp_path))
        async with app.run_test() as pilot:
            await pilot.press("h", "i", "enter")
            await pilot.pause()
            assert app.review["id"] == "r1"
            assert app.query_one("#approval").display
            await pilot.click("#approve")
            await pilot.pause()
            assert app.review is None
            assert not app.query_one("#approval").display
    asyncio.run(scenario())


def test_tui_restores_pending_review(tmp_path):
    async def scenario():
        state = FakeState(tmp_path)
        state.session.request_review({
            "id": "persisted", "call": {"name": "write_file", "arguments": {"path": "a"}},
            "diff": "+new", "target": "", "digest": "", "workspace": str(tmp_path),
        })
        app = CodeRillApp(state)
        async with app.run_test():
            assert app.review["id"] == "persisted"
            assert app.query_one("#approval").display
    asyncio.run(scenario())


def test_tui_switches_sessions_with_shared_state(tmp_path):
    async def scenario():
        state = FakeState(tmp_path)
        other = Session.new("fake", str(tmp_path), state.session.store)
        other.record({"role": "user", "content": "saved message"})
        app = CodeRillApp(state)
        async with app.run_test() as pilot:
            app.query_one("#sessions").value = other.id
            await pilot.pause()
            assert state.session.id == other.id
            assert app.state.agent.on_event == app._runtime_event
    asyncio.run(scenario())


def test_tui_cancel_reaches_active_turn(tmp_path):
    class SlowAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.cancelled = False

        async def run_turn(self, text, attachments):
            await asyncio.sleep(10)

        def cancel_turn(self):
            self.cancelled = True

    async def scenario():
        state = FakeState(tmp_path)
        state.agent = SlowAgent()
        app = CodeRillApp(state)
        async with app.run_test() as pilot:
            await pilot.press("h", "i", "enter")
            await pilot.pause()
            assert app.active_task is not None
            app.action_cancel_turn()
            await pilot.pause()
            assert state.agent.cancelled
            assert app.active_task is None
    asyncio.run(scenario())
