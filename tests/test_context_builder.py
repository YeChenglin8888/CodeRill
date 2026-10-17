import asyncio
import re

from coderill.context import AsyncContextManager, ContextBudgetExceeded
from coderill.context.builder import _semantic_preview
from coderill.context.pipeline import CompactionPipeline
from coderill.session import Session, SessionStore


class FailingSummaryProvider:
    async def complete(self, request):
        raise RuntimeError("summary service failed")


def test_old_dialog_trim_keeps_reasoning_with_its_assistant_message(tmp_path):
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    session.record({"role": "assistant", "content": "answer " * 300, "reasoning": "reason " * 300})
    pipeline = CompactionPipeline(session, [dict(item) for item in session.messages])
    pipeline.l1_old_dialog(1)
    assert pipeline.messages[0] == session.messages[0]


def test_compression_keeps_original_retrievable_and_tool_pairs(tmp_path):
    store = SessionStore(tmp_path)
    session = Session.new("fake", str(tmp_path), store)
    for number in range(8):
        session.record({"role": "user", "content": f"task {number} " + "x" * 500})
        session.record({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{number}", "function": {"name": "read_file", "arguments": "{}"}}
        ]})
        session.record({"role": "tool", "tool_call_id": f"c{number}", "content": "original result " + "z" * 1500})
    original = list(session.messages)
    manager = AsyncContextManager(3000, reserve=200)
    projected = asyncio.run(manager.prepare(session, "system", (), FailingSummaryProvider()))
    assert len(projected) < len(original)
    assert projected[-2:] == original[-2:]
    assert any(event["type"] == "checkpoint" for event in store.events(session.id))
    archive_ids = re.findall(r"archive:([0-9a-f]{64})", str(projected))
    assert archive_ids
    assert any("original result" in session.read_archive(digest) for digest in archive_ids)
    assert Session.load(session.id, store).messages == projected


def test_budget_error_does_not_destroy_original_events(tmp_path):
    store = SessionStore(tmp_path)
    session = Session.new("fake", str(tmp_path), store)
    session.record({"role": "user", "content": "x" * 5000})
    manager = AsyncContextManager(100, reserve=90)
    try:
        asyncio.run(manager.prepare(session, "system", (), FailingSummaryProvider()))
    except ContextBudgetExceeded:
        pass
    else:
        raise AssertionError("oversized request was accepted")
    assert session.messages[0]["content"] == "x" * 5000


def test_request_marks_old_read_stale_after_successful_write(tmp_path):
    import json

    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path))
    for call_id, name, content in (("r1", "read_file", "old bytes"),
                                   ("w", "write_file", "written"),
                                   ("r2", "read_file", "new bytes")):
        args = {"path": "a.txt"}
        session.record({"role": "assistant", "content": "", "tool_calls": [{
            "id": call_id, "function": {"name": name, "arguments": json.dumps(args)},
        }]})
        session.start_tool({"id": call_id, "name": name, "arguments": args})
        session.finish_tool(call_id, "success", content)
        session.record({"role": "tool", "tool_call_id": call_id, "content": content})
    projected = asyncio.run(AsyncContextManager(5000, reserve=100).prepare(
        session, "system", (), FailingSummaryProvider()))
    assert "[stale read:" in projected[1]["content"]
    assert "[stale read:" not in projected[5]["content"]
    assert session.messages[1]["content"] == "old bytes"


def test_old_output_preview_preserves_tail_errors_and_diff_shape():
    build = "starting\n" + "noise\n" * 200 + "AssertionError: expected 2\n"
    assert "AssertionError" in _semantic_preview(build)
    diff = "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
    assert "1 added, 1 removed" in _semantic_preview(diff)
    assert "JSON array with 3 items" in _semantic_preview("[1, 2, 3]")
    search = "\n".join(f"src/a.py:{number}: call_{number}" for number in range(1, 9))
    assert "src/a.py (8 hits)" in _semantic_preview(search)
    assert "src/a.py:8:" in _semantic_preview(search)
    code = "import os\nclass Worker:\n    def run(self):\n        return 1\n"
    assert "L2: class Worker" in _semantic_preview(code)
    assert "Title" in _semantic_preview("<html><title>Title</title><h1>Heading</h1></html>")


def test_task_boundary_without_compression_is_acknowledged_after_reload(tmp_path):
    store = SessionStore(tmp_path)
    session = Session.new("fake", str(tmp_path), store)
    session.record({"role": "user", "content": "first"})
    session.observe_task("new", 0)
    session.record({"role": "user", "content": "second"})
    session.observe_task("new", 1)
    session.record({"role": "user", "content": "continue"})
    session.observe_task("same", 2)
    assert session.task_boundary_pending
    manager = AsyncContextManager(5000, reserve=100)
    asyncio.run(manager.prepare(session, "system", (), FailingSummaryProvider(), force=True))
    assert not session.task_boundary_pending
    assert not Session.load(session.id, store).task_boundary_pending
    assert len([event for event in store.events(session.id)
                if event["type"] == "task_boundary_acknowledged"]) == 1


def test_pipeline_archives_exact_dialog_and_typed_output(tmp_path):
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    dialog = "decision: keep this path\n" + "detail\n" * 250
    search = "\n".join(f"src/a.py:{number}: important_match_{number}" for number in range(1, 80))
    session.record({"role": "user", "content": dialog})
    session.record({"role": "tool", "tool_call_id": "r", "content": search})
    pipeline = CompactionPipeline(session, [dict(item) for item in session.messages])
    pipeline.l1_old_dialog(1)
    pipeline.l2_typed_outputs(2, None)
    assert "decision: keep this path" in pipeline.messages[0]["content"]
    assert "79 search hits" in pipeline.messages[1]["content"]
    for source, projected in zip(session.messages, pipeline.messages):
        digest = re.search(r"archive:([0-9a-f]{64})", projected["content"])[1]
        assert session.read_archive(digest) == source["content"]


def test_pipeline_checkpoint_keeps_full_original_messages(tmp_path):
    import json

    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    session.record({"role": "user", "content": "x" * 1500})
    session.record({"role": "assistant", "content": "finished"})
    pipeline = CompactionPipeline(session, [dict(item) for item in session.messages])
    pipeline.l1_old_dialog(1)

    async def summarize(note):
        return "summary of " + note[:10]

    asyncio.run(pipeline.l4_checkpoint(1, summarize))
    digest = re.search(r"archive:([0-9a-f]{64})", pipeline.messages[0]["content"])[1]
    assert json.loads(session.read_archive(digest)) == session.messages[:1]
    assert pipeline.messages[-1] == session.messages[-1]
