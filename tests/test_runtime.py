"""Offline recovery scenarios for the async turn state machine."""

import asyncio
import threading
from typing import ClassVar

from coderill.agent.runtime import AgentRuntime
from coderill.config import Config
from coderill.permissions import GrantStore, Mode, ReviewManager
from coderill.providers.types import ChatResponse, ChatStreamEvent, ToolCall
from coderill.session import Session, SessionStore
from coderill.taskplan import TaskPlan
from coderill.tools import Tool, build_registry


class Provider:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if response.content:
            yield ChatStreamEvent("text_delta", text=response.content)
        yield ChatStreamEvent("completed", response=response)


def runtime(tmp_path, session, provider, mode=Mode.STANDARD):
    config = Config(api_key="unused", model="fake", workspace=tmp_path, max_turns=3)
    registry = build_registry(tmp_path)
    reviews = ReviewManager(tmp_path, mode=mode, store=GrantStore(tmp_path / "grants.json"))
    return AgentRuntime(config, session, registry, provider, reviews)


def test_benchmark_requires_validation_after_write(tmp_path):
    (tmp_path / "test_dummy.py").write_text("def test_ok(): assert True\n")
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    provider = Provider(
        ChatResponse("", (ToolCall("w", "write_file", {"path": "a.txt", "content": "ok"}),)),
        ChatResponse("done"),
        ChatResponse("", (ToolCall("v", "bash", {"command": "python -m pytest -q test_dummy.py"}),)),
        ChatResponse("verified"),
    )
    runner = runtime(tmp_path, session, provider, Mode.BYPASS)
    runner.config.benchmark = True
    runner.config.max_turns = 8
    result = asyncio.run(runner.run_turn("write and verify"))
    assert result.status == "complete", (result.error, session.finished_calls, session.messages)
    assert result.text == "verified"
    assert "Run a relevant test" in [m["content"] for m in session.messages if m["role"] == "user"][-1]


def test_benchmark_blocks_fourth_identical_failure(tmp_path):
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    bad = lambda call_id: ChatResponse("", (ToolCall(call_id, "read_file", {"path": "missing.txt"}),))
    provider = Provider(bad("a"), bad("b"), bad("c"), bad("d"), ChatResponse("done"))
    runner = runtime(tmp_path, session, provider, Mode.BYPASS)
    runner.config.benchmark = True
    runner.config.max_turns = 8
    result = asyncio.run(runner.run_turn("read"))
    assert result.status == "complete"
    assert "blocked after three" in session.finished_calls["d"]["content"]


def test_benchmark_evidence_survives_reload(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), store)
    provider = Provider(ChatResponse("", (ToolCall("w", "write_file", {"path": "a.txt", "content": "ok"}),)),
                        ChatResponse("done"), ChatResponse("still done"), ChatResponse("done again"))
    first = runtime(tmp_path, session, provider)
    first.config.benchmark = True
    review = asyncio.run(first.run_turn("change it"))
    assert review.status == "waiting"
    restored = Session.load(session.id, store)
    second = runtime(tmp_path, restored, provider)
    second.config.benchmark = True
    second.config.max_turns = 4
    result = asyncio.run(second.resume_turn(review.review["id"], "approve"))
    assert result.status == "failed"
    assert "without a successful validation" in result.error
    assert restored.benchmark_state["needs_validation"]


def test_final_answer_gets_one_plan_reconciliation_attempt(tmp_path):
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path))
    plan = TaskPlan()
    plan.create(["finish the task"], 0)
    provider = Provider(ChatResponse("done"), ChatResponse("blocked by missing input"))
    runner = runtime(tmp_path, session, provider)
    runner.plan = plan
    result = asyncio.run(runner.run_turn("start"))
    assert result.status == "complete"
    assert result.text == "blocked by missing input"
    assert len(provider.requests) == 2
    assert "reconcile unfinished tasks" in provider.requests[1].messages[0].content


def test_approval_rechecks_file_after_restart(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), store)
    target = tmp_path / "a.txt"
    target.write_text("before\n")
    provider = Provider(
        ChatResponse("", (ToolCall("call1", "write_file", {"path": "a.txt", "content": "agent\n"}),)),
        ChatResponse("done"),
    )
    first = asyncio.run(runtime(tmp_path, session, provider).run_turn("change it"))
    assert first.status == "waiting"
    assert "-before" in first.review["diff"]
    target.write_text("human\n")

    resumed = Session.load(session.id, store)
    runner = runtime(tmp_path, resumed, provider)
    stale = asyncio.run(runner.resume_turn(first.review["id"], "approve"))
    assert stale.status == "waiting"
    assert stale.review["id"] != first.review["id"]
    assert "-human" in stale.review["diff"]
    assert target.read_text() == "human\n"

    done = asyncio.run(runner.resume_turn(stale.review["id"], "approve"))
    assert done.status == "complete"
    assert target.read_text() == "agent\n"
    assert [m["role"] for m in resumed.messages] == ["user", "assistant", "tool", "assistant"]
    assert provider.requests[-1].messages[-1].tool_call_id == "call1"


def test_crashed_write_is_marked_unknown_and_never_replayed(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), store)
    session.start_turn()
    session.record({"role": "user", "content": "write"})
    session.record({
        "role": "assistant", "content": "", "tool_calls": [
            {"id": "call1", "type": "function", "function": {
                "name": "write_file", "arguments": '{"path":"a.txt","content":"agent"}'
            }}
        ],
    })
    session.start_tool({"id": "call1", "name": "write_file", "arguments": {"path": "a.txt", "content": "agent"}})
    provider = Provider(ChatResponse("check state first"))
    result = asyncio.run(runtime(tmp_path, Session.load(session.id, store), provider).resume_turn())
    assert result.status == "complete"
    assert not (tmp_path / "a.txt").exists()
    assert "prior execution was interrupted" in provider.requests[0].messages[-1].content


def test_tool_argument_failure_is_paired_and_loop_continues(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), store)
    provider = Provider(
        ChatResponse("", (ToolCall("c1", "read_file", {"offset": 1}),)),
        ChatResponse("recovered"),
    )
    result = asyncio.run(runtime(tmp_path, session, provider, Mode.BYPASS).run_turn("read"))
    assert result.status == "complete"
    assert session.messages[2]["tool_call_id"] == "c1"
    assert "missing required argument" in session.messages[2]["content"]


def test_tool_reported_error_is_recorded_as_failed(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), store)
    provider = Provider(
        ChatResponse("", (ToolCall("c1", "read_file", {"path": "missing.txt"}),)),
        ChatResponse("recovered"),
    )
    events = []
    runner = runtime(tmp_path, session, provider, Mode.BYPASS)
    runner.on_event = events.append
    result = asyncio.run(runner.run_turn("read"))
    assert result.status == "complete"
    assert next(event.status for event in events if event.kind == "tool_result") == "failed"
    assert session.finished_calls["c1"]["status"] == "failed"


def test_read_hooks_apply_without_losing_tool_pair(tmp_path):
    class Hook:
        def __init__(self):
            self.post = []

        def run_pre(self, name, args):
            return "blocked" if args.get("path") == "no.txt" else None

        def run_post(self, name, args, content):
            self.post.append((name, content))

    (tmp_path / "yes.txt").write_text("yes")
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    provider = Provider(ChatResponse("", (
        ToolCall("c1", "read_file", {"path": "no.txt"}),
        ToolCall("c2", "read_file", {"path": "yes.txt"}),
    )), ChatResponse("done"))
    runner = runtime(tmp_path, session, provider, Mode.BYPASS)
    runner.hooks = hook = Hook()
    assert asyncio.run(runner.run_turn("read" )).status == "complete"
    assert session.finished_calls["c1"]["status"] == "denied"
    assert session.finished_calls["c2"]["status"] == "success"
    assert len(hook.post) == 1
    assert [message["tool_call_id"] for message in session.messages if message["role"] == "tool"] == ["c1", "c2"]


def test_request_budget_survives_restart(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), store)
    session.start_turn()
    session.record({"role": "user", "content": "work"})
    for _ in range(3):
        session.record_model_request()
    result = asyncio.run(runtime(tmp_path, Session.load(session.id, store), Provider()).resume_turn())
    assert result.status == "budget"


def test_model_stream_timeout_leaves_no_assistant_or_tool_orphans(tmp_path):
    class SlowProvider:
        async def stream(self, request):
            await asyncio.sleep(1)
            yield ChatStreamEvent("completed", response=ChatResponse("late"))

    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    runner = runtime(tmp_path, session, SlowProvider())
    runner.config.request_timeout = 0.01
    result = asyncio.run(runner.run_turn("hello"))
    assert result.status == "failed"
    assert "timed out" in result.error
    assert [message["role"] for message in session.messages] == ["user"]


def test_model_stream_interruption_does_not_commit_partial_answer(tmp_path):
    class BrokenProvider:
        async def stream(self, request):
            yield ChatStreamEvent("text_delta", text="partial")
            raise RuntimeError("connection lost")

    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    result = asyncio.run(runtime(tmp_path, session, BrokenProvider()).run_turn("hello"))
    assert result.status == "failed"
    assert "connection lost" in result.error
    assert [message["role"] for message in session.messages] == ["user"]


def test_cancel_during_stream_records_cancelled_status(tmp_path):
    class SlowProvider:
        async def stream(self, request):
            await asyncio.sleep(10)
            yield ChatStreamEvent("completed", response=ChatResponse("late"))

    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    runner = runtime(tmp_path, session, SlowProvider())

    async def scenario():
        task = asyncio.create_task(runner.run_turn("hello"))
        await asyncio.sleep(0.01)
        runner.cancel_turn()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert session.last_status == "cancelled"


def test_cancel_async_tool_pairs_started_and_unstarted_calls(tmp_path):
    class SlowTool(Tool):
        name = "slow"
        parameters: ClassVar[dict] = {"type": "object", "properties": {}}

        async def aexecute(self):
            await asyncio.sleep(10)

    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    provider = Provider(ChatResponse("", (
        ToolCall("c1", "slow", {}), ToolCall("c2", "read_file", {"path": "a.txt"}),
    )))
    runner = runtime(tmp_path, session, provider, Mode.BYPASS)
    runner.registry.register(SlowTool())

    async def scenario():
        started = asyncio.Event()
        runner.on_event = lambda event: started.set() if event.kind == "tool_started" else None
        task = asyncio.create_task(runner.run_turn("run"))
        await asyncio.wait_for(started.wait(), 3)
        runner.cancel_turn()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert session.last_status == "cancelled"
    assert "c1" in session.finished_calls, (session.messages, session.incomplete_calls)
    assert session.finished_calls["c1"]["status"] == "unknown"
    assert session.finished_calls["c2"]["status"] == "interrupted"
    assert [message["tool_call_id"] for message in session.messages if message["role"] == "tool"] == ["c1", "c2"]


def test_adjacent_reads_overlap_and_write_orders_batches(tmp_path):
    barrier = threading.Barrier(2)
    events = []

    class Read(Tool):
        name = "probe_read"
        read_only = True
        parameters: ClassVar[dict] = {"type": "object", "properties": {"tag": {"type": "string"}}, "required": ["tag"]}

        def execute(self, tag):
            events.append(f"start:{tag}")
            if tag in ("one", "two"):
                barrier.wait(timeout=2)
            events.append(f"end:{tag}")
            return tag

    class Write(Tool):
        name = "probe_write"
        parameters: ClassVar[dict] = {"type": "object", "properties": {}}

        def execute(self):
            events.append("write")
            return "written"

    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    provider = Provider(ChatResponse("", (
        ToolCall("c1", "probe_read", {"tag": "one"}),
        ToolCall("c2", "probe_read", {"tag": "two"}),
        ToolCall("c3", "probe_write", {}),
        ToolCall("c4", "probe_read", {"tag": "three"}),
    )), ChatResponse("done"))
    runner = runtime(tmp_path, session, provider, Mode.BYPASS)
    runner.registry.register(Read())
    runner.registry.register(Write())
    assert asyncio.run(runner.run_turn("run")).status == "complete"
    assert max(events.index("end:one"), events.index("end:two")) < events.index("write")
    assert events.index("write") < events.index("start:three")


def test_task_boundary_waits_for_followup_then_checkpoints_old_task(tmp_path):
    class ClassifyingProvider(Provider):
        async def complete(self, request):
            if request.messages[0].content.startswith("Summarize"):
                return ChatResponse("Earlier task summary")
            latest = __import__("json").loads(request.messages[1].content)["latest"]
            return ChatResponse('{"decision":"new"}' if latest == "new task" else '{"decision":"same"}')

    store = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), store)
    provider = ClassifyingProvider(ChatResponse("done"), ChatResponse("done"), ChatResponse("done"))
    runner = runtime(tmp_path, session, provider, Mode.BYPASS)
    async def turns():
        assert (await runner.run_turn("old task " + "x" * 6000)).status == "complete"
        old_hash = session.active_task_hash
        assert (await runner.run_turn("new task")).status == "complete"
        assert session.active_task_hash == old_hash
        assert session.task_candidate is not None
        assert (await runner.run_turn("continue new task")).status == "complete"
        return old_hash

    old_hash = asyncio.run(turns())
    assert session.active_task_hash != old_hash
    assert session.task_candidate is None
    assert session.messages[0]["content"].startswith("[Earlier conversation checkpoint")
    restored = Session.load(session.id, store)
    assert restored.active_task_hash == session.active_task_hash
    assert restored.task_boundary_pending is False
    assert restored.read_archive(restored.messages[0]["content"].split("archive:")[1].split("]")[0])


def test_tool_round_budget_pairs_rejected_calls(tmp_path):
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    provider = Provider(ChatResponse("", (ToolCall("c1", "read_file", {"path": "a.txt"}),)))
    runner = runtime(tmp_path, session, provider, Mode.BYPASS)
    runner.config.max_tool_rounds = 0
    result = asyncio.run(runner.run_turn("read"))
    assert result.status == "budget"
    assert session.finished_calls["c1"]["status"] == "failed"
    assert session.turn_tool_rounds == 0
    assert Session.load(session.id, session.store).turn_tool_rounds == 0


def test_cancel_during_hidden_task_classification(tmp_path):
    class SlowClassifier(Provider):
        def __init__(self):
            super().__init__(ChatResponse("first"))
            self.started = asyncio.Event()

        async def complete(self, request):
            self.started.set()
            await asyncio.sleep(10)
            return ChatResponse('{"decision":"same"}')

    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    provider = SlowClassifier()
    runner = runtime(tmp_path, session, provider, Mode.BYPASS)

    async def scenario():
        assert (await runner.run_turn("first task")).status == "complete"
        turn = asyncio.create_task(runner.run_turn("continue"))
        await provider.started.wait()
        runner.cancel_turn()
        return await turn

    assert asyncio.run(scenario()).status == "cancelled"
    assert session.last_status == "cancelled"
