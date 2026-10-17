"""Both vendor adapters satisfy the same request and response contract."""

import asyncio
from types import SimpleNamespace

import pytest

from coderill.providers.anthropic_adapter import AnthropicChatProvider
from coderill.providers.errors import ProviderError, ProviderErrorKind, classify_error
from coderill.providers.openai_adapter import OpenAIChatProvider
from coderill.providers.types import ChatMessage, ChatRequest, ToolCall, ToolDefinition


class Endpoint:
    def __init__(self, response):
        self.response = response
        self.params = None

    async def create(self, **params):
        self.params = params
        return self.response


def request():
    return ChatRequest(
        messages=(ChatMessage("system", "Be concise"), ChatMessage("user", "Read a file")),
        tools=(ToolDefinition("read_file", "Read", {"type": "object", "properties": {"path": {"type": "string"}}}),),
    )


@pytest.mark.parametrize("vendor", ["openai", "anthropic"])
def test_complete_normalizes_tool_calls(vendor):
    if vendor == "openai":
        endpoint = Endpoint({
            "choices": [{"message": {"content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}}
            ]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 4},
        })
        provider = OpenAIChatProvider(model="test", api_key="unused", client=SimpleNamespace(
            chat=SimpleNamespace(completions=endpoint)
        ))
    else:
        endpoint = Endpoint({
            "content": [{"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "a.py"}}],
            "stop_reason": "tool_use", "usage": {"input_tokens": 8, "output_tokens": 4},
        })
        provider = AnthropicChatProvider(model="test", api_key="unused", client=SimpleNamespace(messages=endpoint))

    result = asyncio.run(provider.complete(request()))
    assert result.tool_calls == (ToolCall("c1", "read_file", {"path": "a.py"}),)
    assert result.finish_reason == "tool_calls"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (8, 4)
    assert endpoint.params["model"] == "test"
    assert endpoint.params["tools"]


def test_openai_stream_reassembles_partial_tool_call():
    class Stream:
        def __init__(self):
            self.chunks = iter([
                {"choices": [{"delta": {"content": "hello", "tool_calls": [
                    {"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": '{"path":'}}
                ]}}]},
                {"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "function": {"arguments": '"a.py"}'}}
                ]}, "finish_reason": "tool_calls"}]},
            ])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.chunks)
            except StopIteration:
                raise StopAsyncIteration from None

        async def close(self):
            pass

    endpoint = Endpoint(Stream())
    provider = OpenAIChatProvider(model="test", api_key="unused", client=SimpleNamespace(
        chat=SimpleNamespace(completions=endpoint)
    ))

    async def collect():
        return [event async for event in provider.stream(request())]

    events = asyncio.run(collect())
    assert events[0].kind == "text_delta"
    assert events[-1].response.tool_calls == (ToolCall("c1", "read_file", {"path": "a.py"}),)


def test_deepseek_disables_default_thinking_for_tool_replay():
    endpoint = Endpoint({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    client = SimpleNamespace(chat=SimpleNamespace(completions=endpoint))
    provider = OpenAIChatProvider(model="deepseek-flash", api_key="unused",
                                  base_url="https://api.deepseek.com", client=client)
    asyncio.run(provider.complete(request()))
    assert endpoint.params["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "extra_body" not in OpenAIChatProvider(model="test", api_key="unused", client=client)._params(request())


def test_anthropic_message_conversion_pairs_tool_result():
    from coderill.providers.wire import anthropic_messages

    req = ChatRequest(messages=(
        ChatMessage("assistant", tool_calls=(ToolCall("c1", "read_file", {"path": "a.py"}),)),
        ChatMessage("tool", "contents", tool_call_id="c1"),
    ))
    _, messages = anthropic_messages(req)
    assert messages[0]["content"][0]["type"] == "tool_use"
    assert messages[1]["content"][0] == {"type": "tool_result", "tool_use_id": "c1", "content": "contents"}


def test_anthropic_stream_reassembles_tool_call_and_usage():
    class Stream:
        def __init__(self):
            self.events = iter([
                {"type": "message_start", "message": {"usage": {"input_tokens": 9}}},
                {"type": "content_block_start", "index": 0, "content_block": {
                    "type": "tool_use", "id": "c1", "name": "read_file", "input": {},
                }},
                {"type": "content_block_delta", "index": 0, "delta": {
                    "type": "input_json_delta", "partial_json": '{"path":"a.py"}',
                }},
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 3}},
            ])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.events)
            except StopIteration:
                raise StopAsyncIteration from None

        async def close(self):
            pass

    provider = AnthropicChatProvider(model="test", api_key="unused", client=SimpleNamespace(messages=Endpoint(Stream())))

    async def collect():
        return [event async for event in provider.stream(request())]

    response = asyncio.run(collect())[-1].response
    assert response.tool_calls == (ToolCall("c1", "read_file", {"path": "a.py"}),)
    assert (response.usage.input_tokens, response.usage.output_tokens) == (9, 3)


def test_provider_error_keeps_its_kind_after_stream_classification():
    error = ProviderError(ProviderErrorKind.RATE_LIMIT, "slow down")
    assert classify_error(error) is error


def test_rate_limit_retries_before_committing_response():
    class RateLimited(Exception):
        status_code = 429

    class FlakyEndpoint:
        def __init__(self):
            self.calls = 0

        async def create(self, **params):
            self.calls += 1
            if self.calls == 1:
                raise RateLimited("slow down")
            return {"choices": [{"message": {"content": "recovered"}, "finish_reason": "stop"}]}

    endpoint = FlakyEndpoint()
    provider = OpenAIChatProvider(model="test", api_key="unused", attempts=2, client=SimpleNamespace(
        chat=SimpleNamespace(completions=endpoint)
    ))
    assert asyncio.run(provider.complete(request())).content == "recovered"
    assert endpoint.calls == 2
