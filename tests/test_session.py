"""Tests for the event-sourced session store."""

import copy

import pytest

from coderill.session import Session, SessionStore
from coderill.session.projection import project_events


def _store(tmp_path):
    return SessionStore(tmp_path)


def test_messages_round_trip_through_the_log(tmp_path):
    store = _store(tmp_path)
    session = Session.new("test-model", "/workspace", store)
    session.record({"role": "user", "content": "hi"})
    session.record({"role": "assistant", "content": "hello"})

    loaded = Session.load(session.id, store)
    assert loaded.model == "test-model"
    assert loaded.messages == session.messages
    assert loaded.turns == 1


def test_the_log_is_append_only(tmp_path):
    store = _store(tmp_path)
    session = Session.new("m", "/ws", store)
    session.record({"role": "user", "content": "a"})
    session.record({"role": "user", "content": "b"})

    events = store.events(session.id)
    assert [event["type"] for event in events] == ["start", "message", "message"]
    assert [event["seq"] for event in events] == [1, 2, 3]


def test_failed_append_does_not_advance_projection(tmp_path, monkeypatch):
    store = _store(tmp_path)
    session = Session.new("m", "/ws", store)
    original = store.append

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(store, "append", fail)
    with pytest.raises(OSError, match="disk full"):
        session.record({"role": "user", "content": "lost"})
    assert session.messages == []
    monkeypatch.setattr(store, "append", original)
    session.record({"role": "user", "content": "kept"})
    assert [event["seq"] for event in store.events(session.id)] == [1, 2]


def test_replay_projection_does_not_modify_events(tmp_path):
    store = _store(tmp_path)
    session = Session.new("m", "/ws", store)
    session.start_background({"id": "job", "status": "running", "description": "work"})
    events = store.events(session.id)
    original = copy.deepcopy(events)
    assert project_events(events)["background_tasks"]["job"]["status"] == "interrupted"
    assert events == original


def test_checkpoint_supersedes_earlier_messages_without_erasing_them(tmp_path):
    store = _store(tmp_path)
    session = Session.new("m", "/ws", store)
    session.record({"role": "user", "content": "old"})
    session.checkpoint([{"role": "user", "content": "summary"}])
    session.record({"role": "assistant", "content": "after"})

    loaded = Session.load(session.id, store)
    assert loaded.messages == [{"role": "user", "content": "summary"}, {"role": "assistant", "content": "after"}]
    # The superseded message is still in the durable log.
    assert any(event.get("type") == "message" for event in store.events(session.id))


def test_list_all_is_newest_first(tmp_path):
    store = _store(tmp_path)
    first = Session.new("m", "/ws", store)
    first.record({"role": "user", "content": "1"})
    second = Session.new("m", "/ws", store)
    second.record({"role": "user", "content": "2"})

    ids = [session.id for session in Session.list_all(store)]
    assert ids == [second.id, first.id]


def test_list_all_skips_unreadable_logs(tmp_path):
    store = _store(tmp_path)
    (tmp_path / "broken.jsonl").write_text("not json at all\n")
    good = Session.new("m", "/ws", store)

    assert [session.id for session in Session.list_all(store)] == [good.id]


def test_load_rejects_an_unknown_schema(tmp_path):
    store = _store(tmp_path)
    (tmp_path / "old.jsonl").write_text('{"v": 0, "seq": 1, "ts": "now", "type": "start"}\n')
    with pytest.raises(ValueError):
        Session.load("old", store)


def test_fork_copies_the_conversation_into_a_new_session(tmp_path):
    store = _store(tmp_path)
    original = Session.new("m", "/ws", store)
    original.record({"role": "user", "content": "hello"})
    original.record({"role": "assistant", "content": "hi"})
    original.record_plan({
        "mode": "linear", "revision": 1,
        "tasks": [{"id": "t1", "content": "fix bug", "status": "pending", "owner": None, "depends_on": []}],
    })

    clone = original.fork(store)
    assert clone.id != original.id
    assert clone.messages == original.messages
    assert clone.model == original.model
    assert Session.load(clone.id, store).plan_snapshot == original.plan_snapshot


def test_fork_keeps_archived_text_and_attachments_readable(tmp_path):
    store = _store(tmp_path)
    original = Session.new("m", "/ws", store)
    digest = original.archive_text("old full tool output")
    original.checkpoint([{"role": "user", "content": f"[archive:{digest}]"}])
    attachment_file = tmp_path / "note.txt"
    attachment_file.write_text("attached content", encoding="utf-8")
    attachment = original.attach_file(attachment_file)

    clone = Session.load(original.fork(store).id, store)
    assert clone.read_archive(digest) == "old full tool output"
    assert clone.store.read_blob(clone.id, attachment["digest"]) == b"attached content"
    assert any(event.get("type") == "archive" for event in store.events(clone.id))


def test_transcript_redacts_secrets_and_skips_tool_noise(tmp_path):
    store = _store(tmp_path)
    session = Session.new("m", "/ws", store)
    session.record({"role": "user", "content": "key is sk-abcdefghijklmnop1234"})
    session.record({"role": "tool", "content": "raw tool output"})

    text = session.transcript()
    assert "sk-abcdefghijklmnop1234" not in text
    assert "[redacted]" in text
    assert "raw tool output" not in text


def test_v1_log_can_continue_with_v2_events(tmp_path):
    store = _store(tmp_path)
    path = store.path("old")
    path.write_text(
        '{"v":1,"seq":1,"ts":"now","type":"start","model":"m","workspace":"/ws"}\n'
        '{"v":1,"seq":2,"ts":"now","type":"message","message":{"role":"user","content":"hi"}}\n'
    )
    session = Session.load("old", store)
    session.record({"role": "assistant", "content": "hello"})
    assert [event["v"] for event in store.events("old")] == [1, 1, 2]
    assert Session.load("old", store).messages[-1]["content"] == "hello"


def test_middle_corruption_is_reported_and_incomplete_tail_is_preserved(tmp_path):
    store = _store(tmp_path)
    session = Session.new("m", "/ws", store)
    path = store.path(session.id)
    with path.open("ab") as handle:
        handle.write(b'{"v":2,"seq":2')
    assert len(store.events(session.id)) == 1
    session.record({"role": "user", "content": "after restart"})
    assert len(store.events(session.id)) == 2
    assert path.with_suffix(".incomplete-tail").exists()
    path.write_text(path.read_text() + "{broken}\n" + '{"v":2,"seq":3,"type":"touch"}\n')
    with pytest.raises(ValueError, match="damaged event"):
        store.events(session.id)


def test_pending_review_and_unknown_tool_result_replay(tmp_path):
    store = _store(tmp_path)
    session = Session.new("m", "/ws", store)
    session.start_tool({"id": "c1", "name": "write_file", "arguments": {"path": "a"}})
    session.request_review({"id": "r1", "call_id": "c1", "digest": "old"})
    loaded = Session.load(session.id, store)
    assert loaded.pending_review["id"] == "r1"
    assert "c1" in loaded.incomplete_calls
    loaded.resolve_review("r1", "deny")
    loaded.finish_tool("c1", "denied", "refused")
    assert Session.load(session.id, store).incomplete_calls == {}


def test_driver_lock_excludes_second_driver(tmp_path):
    store = _store(tmp_path)
    with store.driver_lock("same"), pytest.raises(RuntimeError, match="already running"), store.driver_lock("same"):
        pass
