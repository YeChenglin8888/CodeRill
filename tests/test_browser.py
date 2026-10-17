"""Browser protocol, execution boundaries, and deadline tests; no real browser or API.

Adapted to CodeRill's interfaces from the original CoreCoder test suite. The
tests that exercise the shared agent loop or CoreCoder-specific LLM internals
were dropped, since CodeRill's loop and client are different implementations.
"""

import copy
import json
import threading
import types
from dataclasses import dataclass, field
from unittest import mock

import pytest

from coderill import browser as b
from coderill.config import Config
from coderill.tools.browser import BrowserTaskTool


@dataclass
class LLMResponse:
    """Minimal stand-in for the client's non-streaming response."""

    content: str = ""
    tool_calls: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


def snapshot():
    return {"snapshot_id": 1, "url": "http://localhost/test", "title": "Test", "text": "Search",
            "text_truncated": False, "omitted_actions": 0, "limitations": [],
            "scroll": {"x": 0, "y": 0, "height": 720, "viewport": 720},
            "actions": [{"id": "e1", "kind": "fill", "node": 1, "label": "Search", "value": ""},
                        {"id": "e2", "kind": "click", "node": 2, "label": "Go", "value": ""}]}


def choice_response(request, operation="DONE", target=None):
    answers = {}
    for head, question in request["questions"].items():
        if head == "operation":
            choice = operation
        elif operation in b._TARGET_HEADS and head == b._TARGET_HEADS[operation][0] and target:
            choice = target
        else:
            choice = next(iter(question["criteria"]))
        answers[head] = {"type": "choice", "choice": choice, "confidence": 1,
                         "probabilities": {key: int(key == choice) for key in question["criteria"]}}
    return {"model": "jev-test", "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 2}}


def text_llm(content='{"text":"cats"}'):
    return types.SimpleNamespace(model="test-text", chat=mock.Mock(return_value=LLMResponse(
        content=content, prompt_tokens=5, completion_tokens=3)))


@pytest.mark.parametrize("arguments", [
    {"url": "file:///test"}, {"url": "https://user:pass@example.com"}, {"url": "http://example.com:invalid"},
    {"url": "http://example.com/ a"}, {"url": "http://example.com\\@evil.test"}, {"url": 4},
    {"goal": " "}, {"goal": None}, {"max_steps": True}, {"max_steps": 0}, {"max_steps": 61},
    {"timeout_seconds": False}, {"timeout_seconds": 301}, {"timeout_seconds": "120"},
])
def test_inputs_are_validated_before_launch(arguments):
    tool = BrowserTaskTool(text_llm(), Config(typesafe_api_key="key"))
    with mock.patch.object(b, "Browser") as launch:
        result = json.loads(tool.execute(**{"url": "https://example.com", "goal": "Search", **arguments}))
    assert result["stop_reason"] == "invalid_input"
    launch.assert_not_called()


def test_configuration_fails_before_launch():
    tool = BrowserTaskTool(text_llm(), Config())
    assert json.loads(tool.execute("https://example.com", "Search"))["stop_reason"] == "config_error"


def test_request_has_independent_heads_and_excludes_unavailable_operations():
    request = b.build_request(snapshot(), "Search cats", [], "jev-latest")
    assert set(request["questions"]) == {"operation", "click_target", "type_text_target"}
    assert set(request["questions"]["operation"]["criteria"]) == {"CLICK", "TYPE_TEXT", "WAIT", "DONE", "BLOCKED"}
    assert request["state"]["user_goal"] == "Search cats"
    assert "untrusted_page" in request["state"]
    operation, target = b.decode_response(choice_response(request, "CLICK"), request)
    assert operation == "CLICK" and target["id"] == "e2"


@pytest.mark.parametrize("bad", ["type", "unknown", "missing", "extra", "nan", "boolean", "sum", "maximum", "confidence", "usage"])
def test_malformed_choices_are_rejected(bad):
    request = b.build_request(snapshot(), "Search", [], "jev-latest")
    response = choice_response(request)
    answer = response["answers"]["operation"]
    if bad == "type":
        answer["type"] = "score"
    elif bad == "unknown":
        answer["choice"] = "EXEC_JS"
    elif bad == "missing":
        answer["probabilities"].pop("WAIT")
    elif bad == "extra":
        answer["probabilities"]["EXEC_JS"] = 0
    elif bad == "nan":
        answer["probabilities"]["WAIT"] = float("nan")
    elif bad == "boolean":
        answer["probabilities"]["WAIT"] = False
    elif bad == "sum":
        answer["probabilities"]["DONE"] = 0.5
    elif bad == "maximum":
        answer["choice"] = "WAIT"
    elif bad == "confidence":
        answer["confidence"] = 2
    else:
        response["usage"]["input_tokens"] = True
    with pytest.raises(b.TaskStopped, match="invalid choice"):
        b.decode_response(response, request)


class FakeBrowser:
    def __init__(self):
        self.state = snapshot()
        self.deadline = None
        self.closed = False
        self.element = mock.Mock()
        self.element.as_element.return_value = self.element
        self.handle = mock.Mock()
        self.handle.get_property.return_value = self.element
        self.page = types.SimpleNamespace(goto=mock.Mock(), wait_for_timeout=mock.Mock(), evaluate=mock.Mock())

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def observe(self):
        self.check()
        return copy.deepcopy(self.state)

    def check(self):
        b.remaining_time(self.deadline)

    def fresh(self, _):
        return True

    def target(self, *_):
        return self.handle


@pytest.fixture
def fake_task(monkeypatch):
    browser = FakeBrowser()
    llm = text_llm()
    requests, operations = [], ["DONE"]

    def respond(url, *, json, timeout):
        payload = json
        requests.append(payload)
        operation = operations.pop(0) if len(operations) > 1 else operations[0]
        return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: choice_response(payload, operation))

    client = mock.MagicMock()
    client.__enter__.return_value = client
    client.post.side_effect = respond

    def launch(channel, deadline):
        browser.deadline = deadline
        return browser

    monkeypatch.setattr(b, "Browser", launch)
    monkeypatch.setitem(__import__("sys").modules, "httpx", types.SimpleNamespace(Client=lambda **_: client, HTTPError=OSError))
    tool = BrowserTaskTool(llm, Config(typesafe_api_key="test-key"))
    return tool, browser, llm, requests, operations


def test_done_returns_evidence_and_usage_not_verified_success(fake_task):
    tool, browser, llm, requests, _ = fake_task
    result = json.loads(tool.execute("http://localhost/test", "Search"))
    assert result["stop_reason"] == "model_done" and "success" not in result
    assert result["evidence_fresh"] and result["visible_text"] == "Search" and browser.closed
    assert result["usage"]["jev"]["input_tokens"] == 10 and len(requests) == 1
    llm.chat.assert_not_called()


def test_side_effect_failure_is_never_replayed(fake_task):
    tool, browser, _, requests, operations = fake_task
    operations[:] = ["CLICK", "DONE"]
    browser.element.click.side_effect = RuntimeError("failed after dispatch")
    result = json.loads(tool.execute("http://localhost/test", "Click Go"))
    assert result["stop_reason"] == "execution_uncertain" and len(requests) == 1
    assert result["recent_actions"][0]["status"] == "uncertain"
    assert browser.closed and browser.element.click.call_count == 1


def test_stale_decisions_and_wait_consume_budget(fake_task):
    tool, browser, _, requests, operations = fake_task
    operations[:] = ["CLICK"]
    browser.target = lambda *_: None
    result = json.loads(tool.execute("http://localhost/test", "Click Go", max_steps=2))
    assert result["stop_reason"] == "max_steps" and len(requests) == 2 and not result["recent_actions"]


def test_wait_does_not_trigger_no_progress(fake_task):
    tool, _, _, _, operations = fake_task
    operations[:] = ["WAIT", "WAIT", "WAIT", "DONE"]
    assert json.loads(tool.execute("http://localhost/test", "Wait"))["stop_reason"] == "model_done"


def test_three_unchanged_actions_stop(fake_task):
    tool, browser, _, _, operations = fake_task
    operations[:] = ["CLICK"]
    result = json.loads(tool.execute("http://localhost/test", "Click Go"))
    assert result["stop_reason"] == "no_progress" and browser.element.click.call_count == 3
    assert all(call.args[0] <= 100 for call in browser.page.wait_for_timeout.call_args_list)


@pytest.mark.parametrize("content,reason", [(' {"blocked":"Missing destination"}', "needs_input"),
                                            ('{"text":"x","extra":1}', "invalid_response"),
                                            ('not JSON', "invalid_response")])
def test_text_is_isolated_and_blocked_or_invalid_text_is_not_typed(fake_task, content, reason):
    tool, browser, llm, _, operations = fake_task
    operations[:] = ["TYPE_TEXT"]
    llm.chat.return_value.content = content
    result = json.loads(tool.execute("http://localhost/test", "Enter destination"))
    assert result["stop_reason"] == reason
    browser.element.fill.assert_not_called()
    assert llm.chat.call_args.kwargs["tools"] is None
    assert result["usage"]["text"]["input_tokens"] == 5


def test_text_usage_and_history_do_not_leak_entered_value(fake_task):
    tool, browser, _, _, operations = fake_task
    operations[:] = ["TYPE_TEXT", "DONE"]
    result = json.loads(tool.execute("http://localhost/test", "Enter cats"))
    browser.element.fill.assert_called_once()
    assert result["usage"]["text"]["output_tokens"] == 3
    assert "cats" not in json.dumps(result["recent_actions"])


def test_expiry_during_decision_starts_no_action(fake_task, monkeypatch):
    tool, browser, _, _, _ = fake_task
    clock = [0.0]
    monkeypatch.setattr(b.time, "monotonic", lambda: clock[0])

    def late_decision(*_):
        clock[0] = 2
        return "CLICK", snapshot()["actions"][1]

    monkeypatch.setattr(b, "choose_action", late_decision)
    result = json.loads(tool.execute("http://localhost/test", "Go", timeout_seconds=1))
    assert result["stop_reason"] == "timeout" and not result["evidence_fresh"]
    assert result["visible_text"] == "Search" and browser.closed
    browser.element.click.assert_not_called()


def test_keyboard_interrupt_cleans_up_and_propagates(fake_task, monkeypatch):
    tool, browser, _, _, _ = fake_task
    monkeypatch.setattr(b, "choose_action", mock.Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        tool.execute("http://localhost/test", "Go")
    assert browser.closed


def test_profile_lock_is_released_on_exit():
    # The lock table is module state; a stale lock would block the next task.
    assert all(not lock.locked() for lock in b._PROFILE_LOCKS.values())
    assert isinstance(threading.current_thread(), threading.Thread)
