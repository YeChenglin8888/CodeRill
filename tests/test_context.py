"""Tests for context compression."""

from coderill.context import ContextManager, _safe_split, estimate_tokens


def test_estimate_tokens_grows_with_content():
    small = [{"role": "user", "content": "hi"}]
    big = [{"role": "user", "content": "x" * 4000}]
    assert estimate_tokens(big) > estimate_tokens(small)


def test_no_compression_under_budget():
    manager = ContextManager(max_tokens=1000)
    messages = [{"role": "user", "content": "hi"}]
    assert manager.maybe_compress(messages, None) is False


def test_large_tool_output_is_snipped():
    manager = ContextManager(max_tokens=500, threshold=0.5)
    # The tool result must sit outside the recent-keep window for the snip layer
    # to touch it, so pad the conversation with newer messages.
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "calling"},
        {"role": "tool", "tool_call_id": "1", "content": "z" * 20_000},
    ]
    messages += [{"role": "assistant", "content": "filler"} for _ in range(8)]

    changed = manager.maybe_compress(messages, None)
    assert changed is True
    assert "snipped" in messages[2]["content"]


def test_safe_split_never_starts_on_a_tool_result():
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "r"},
        {"role": "assistant", "content": "done"},
    ]
    index = _safe_split(messages, 2)
    assert messages[index]["role"] != "tool"
