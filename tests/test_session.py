"""Tests for the event-sourced session store."""

import pytest

from coderill.session import Session, SessionStore


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

    clone = original.fork(store)
    assert clone.id != original.id
    assert clone.messages == original.messages
    assert clone.model == original.model


def test_transcript_redacts_secrets_and_skips_tool_noise(tmp_path):
    store = _store(tmp_path)
    session = Session.new("m", "/ws", store)
    session.record({"role": "user", "content": "key is sk-abcdefghijklmnop1234"})
    session.record({"role": "tool", "content": "raw tool output"})

    text = session.transcript()
    assert "sk-abcdefghijklmnop1234" not in text
    assert "[redacted]" in text
    assert "raw tool output" not in text
