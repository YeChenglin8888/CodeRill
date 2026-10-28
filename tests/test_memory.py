import asyncio
import json
import time

from coderill.agent.runtime import AgentRuntime
from coderill.config import Config
from coderill.memory import MemoryStore
from coderill.permissions import ReviewManager
from coderill.providers.types import ChatResponse, ChatStreamEvent
from coderill.session import Session, SessionStore
from coderill.tools import Registry
from coderill.tools.memory import MemoryReadTool
from examples.memory_benchmark import evaluate


def test_recall_correction_deletion_and_isolation(tmp_path):
    store = MemoryStore(tmp_path / "one", tmp_path / "memory")
    other = MemoryStore(tmp_path / "two", tmp_path / "memory")
    first = store.add(key="test command", kind="constraint", content="Use pytest for parser tests",
                      evidence="Use pytest for parser tests", source="session:1:event:1", source_type="user")
    assert store.search("parser pytest")[0]["id"] == first
    assert other.search("parser pytest") == []
    second = store.correct(first, "Use unittest for parser tests")
    assert [item["id"] for item in store.search("parser tests")] == [second]
    assert store.get(first) is None
    assert store.delete(second)
    assert store.search("parser tests") == []
    assert store.add(key="test command", kind="constraint", content="Use pytest for parser tests",
                     evidence="Use pytest for parser tests", source="session:1:event:1", source_type="user") is None
    assert store.search("parser pytest") == []


def test_conflict_and_chinese_retrieval(tmp_path):
    store = MemoryStore(tmp_path, tmp_path / "db")
    first = store.add(key="部署环境", kind="decision", content="部署环境使用测试服务器",
                      evidence="部署环境使用测试服务器", source="a", source_type="user")
    second = store.add(key="部署环境", kind="decision", content="部署环境使用本地服务器",
                       evidence="部署环境使用本地服务器", source="b", source_type="assistant")
    assert {item["id"] for item in store.search("部署环境")} == {first, second}
    assert store.get(second)["status"] == "conflict"
    third = store.correct(first, "部署环境使用生产服务器")
    assert [item["id"] for item in store.search("部署环境")] == [third]


def test_correction_resolves_conflict_even_when_repeating_value(tmp_path):
    store = MemoryStore(tmp_path, tmp_path / "db")
    first = store.add(key="tests", kind="decision", content="Use pytest for parser tests.",
                      evidence="Use pytest for parser tests.", source="a", source_type="user")
    store.add(key="tests", kind="decision", content="Use unittest for parser tests.",
              evidence="Use unittest for parser tests.", source="b", source_type="assistant")
    latest = store.correct(first, "Use unittest for parser tests.")
    assert [item["id"] for item in store.search("parser tests")] == [latest]


def test_import_is_idempotent_and_does_not_restore_deleted(tmp_path):
    sessions = SessionStore(tmp_path / "sessions")
    project = tmp_path / "project"
    session = Session.new("fake", str(project), sessions)
    session.record({"role": "user", "content": "Always use pytest for parser tests."})
    other = Session.new("fake", str(tmp_path / "other"), sessions)
    other.record({"role": "user", "content": "An unrelated private project rule."})
    store = MemoryStore(project, tmp_path / "db")
    store.import_history(sessions)
    item = store.search("parser pytest")[0]
    assert item["source"].endswith(":event:2")
    assert store.search("unrelated private") == []
    assert store.delete(item["id"])
    store.import_history(sessions)
    assert store.search("parser pytest") == []


def test_import_does_not_revive_corrected_source(tmp_path):
    sessions = SessionStore(tmp_path / "sessions")
    session = Session.new("fake", str(tmp_path), sessions)
    session.record({"role": "user", "content": "Use pytest for parser tests."})
    store = MemoryStore(tmp_path, tmp_path / "db")
    first = store.add(key="tests", kind="constraint", content="Use pytest for parser tests.",
                      evidence="Use pytest for parser tests.", source=f"session:{session.id}:event:2", source_type="user")
    store.correct(first, "Use unittest for parser tests.")
    store.import_history(sessions)
    assert [item["content"] for item in store.search("parser tests")] == ["Use unittest for parser tests."]


def test_extract_requires_exact_evidence(tmp_path):
    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    session.record({"role": "user", "content": "Always run pytest before completing changes."})
    session.record({"role": "assistant", "content": "I will run the tests."})

    class Provider:
        async def complete(self, request):
            return ChatResponse(json.dumps({"memories": [
                {"key": "tests", "kind": "constraint", "content": "Run pytest before completion",
                 "index": 0, "evidence": "Always run pytest before completing changes.", "update": False},
                {"key": "fake", "kind": "decision", "content": "An invented rule", "index": 0,
                 "evidence": "This was never actually said", "update": False},
            ]}))

    store = MemoryStore(tmp_path, tmp_path / "memory")
    runtime = AgentRuntime(Config(workspace=tmp_path), session, Registry(tmp_path), Provider(),
                           ReviewManager(tmp_path), memory=store)
    runtime._turn_deadline = time.monotonic() + 10
    asyncio.run(runtime._remember_turn())
    items = store.list()
    assert len(items) == 1
    assert items[0]["source"] == f"session:{session.id}:event:2"
    assert MemoryReadTool(store).execute(items[0]["id"]).find("Always run pytest") >= 0


def test_evaluation_reports_stale_results(tmp_path):
    cases = [{"id": "update", "query": "parser tests", "records": [
        {"key": "test", "content": "Use pytest for parser tests."},
        {"key": "test", "content": "Use unittest for parser tests.", "update": True}],
        "expected": "Use unittest for parser tests.", "stale": ["Use pytest for parser tests."]}]
    assert evaluate(cases, "lexical")["stale_cases"] == 1
    assert evaluate(cases, "lifecycle")["stale_cases"] == 0


def test_semantic_retrieval_caches_and_removes_embeddings(tmp_path):
    store = MemoryStore(tmp_path, tmp_path / "memory")
    item_id = store.add(key="tests", kind="constraint", content="Execute the test suite before release",
                        evidence="Execute the test suite before release", source="s", source_type="user")

    class Model:
        def __init__(self):
            self.calls = []

        def encode(self, texts, normalize_embeddings=True):
            self.calls.append(texts)
            return [[1.0, 0.0] for _ in texts]

    model = Model()
    store._model_checked = True
    store._model = model
    assert store.search("验证质量")[0]["id"] == item_id
    assert store.search("验证质量")[0]["id"] == item_id
    assert len(model.calls[0]) == 2 and len(model.calls[1]) == 1
    assert store.delete(item_id)
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0


def test_runtime_remembers_across_sessions(tmp_path):
    store = MemoryStore(tmp_path, tmp_path / "memory")
    sessions = SessionStore(tmp_path / "sessions")

    class Provider:
        def __init__(self):
            self.requests = []

        async def stream(self, request):
            self.requests.append(request)
            yield ChatStreamEvent("completed", response=ChatResponse("Finished."))

        async def complete(self, request):
            if "Always run pytest" in request.messages[-1].content:
                return ChatResponse(json.dumps({"memories": [{"key": "parser tests", "kind": "constraint",
                    "content": "Always run pytest for parser tests.", "index": 0,
                    "evidence": "Always run pytest for parser tests.", "update": False}]}))
            return ChatResponse('{"memories":[]}')

    def runner(provider):
        session = Session.new("fake", str(tmp_path), sessions)
        return AgentRuntime(Config(workspace=tmp_path), session, Registry(tmp_path), provider,
                            ReviewManager(tmp_path), memory=store)

    first = Provider()
    assert asyncio.run(runner(first).run_turn("Always run pytest for parser tests.")).status == "complete"
    second = Provider()
    assert asyncio.run(runner(second).run_turn("What are the parser tests rules?")).status == "complete"
    assert "Always run pytest for parser tests." in second.requests[0].messages[0].content
