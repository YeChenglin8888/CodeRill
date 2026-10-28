"""Qualification cases run through the read-only agent and real boundary prompt."""

import asyncio
from pathlib import Path

from coderill.config import Config
from coderill.providers.types import ChatResponse, ChatStreamEvent, ToolCall
from examples import routing_qualification as qualification


def test_fixed_qualification_manifest_has_required_independent_cases():
    path = Path(__file__).parents[1] / "evals" / "routing_qualification.json"
    cases = qualification.load_cases(path)
    assert len(cases["read_only"]) == 100
    assert len(cases["boundaries"]) == 50
    assert len({row["id"] for row in cases["read_only"]}) == 100


def test_qualification_uses_readonly_runtime_and_boundary_classifier(monkeypatch, tmp_path):
    observed = []
    original_run = qualification.AgentRuntime.run_turn

    async def record_run(runtime, *args, **kwargs):
        result = await original_run(runtime, *args, **kwargs)
        observed.append((result.status, result.text, result.error, runtime.session.finished_calls))
        return result

    monkeypatch.setattr(qualification.AgentRuntime, "run_turn", record_run)
    class Provider:
        def __init__(self):
            self.calls = 0

        async def stream(self, _request):
            self.calls += 1
            response = (ChatResponse("", (ToolCall("read", "read_file", {"path": "record.json"}),))
                        if self.calls == 1 else ChatResponse("TOKEN-00-00"))
            yield ChatStreamEvent("completed", response=response)

        async def complete(self, _request):
            return ChatResponse('{"decision":"same"}')

    monkeypatch.setattr(qualification, "create_provider", lambda _config: Provider())
    config = Config(api_key="unused", workspace=tmp_path)
    read = {"id": "read", "prompt": "Read record.json", "expected": "TOKEN-00-00",
            "files": {"record.json": '{"answer":"TOKEN-00-00"}'}}
    boundary = {"id": "boundary", "previous": [{"role": "user", "content": "Inspect code"}],
                "latest": "Continue inspecting code", "decision": "same"}
    assert asyncio.run(qualification.read_only_case(config, read)), observed
    assert asyncio.run(qualification.boundary_case(config, boundary))
