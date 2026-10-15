"""Offline, repeatable approval and crash-recovery demonstration."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from coderill.agent.runtime import AgentRuntime
from coderill.config import Config
from coderill.permissions import GrantStore, Mode, ReviewManager
from coderill.providers.types import ChatResponse, ChatStreamEvent, ProviderCapabilities, ToolCall
from coderill.session import Session, SessionStore
from coderill.tools import build_registry


class FakeProvider:
    model = "offline-demo"
    capabilities = ProviderCapabilities()

    def __init__(self, *responses: ChatResponse) -> None:
        self.responses = list(responses)

    async def stream(self, request):
        response = self.responses.pop(0)
        yield ChatStreamEvent("completed", response=response)


def runner(root: Path, session: Session, provider: FakeProvider) -> AgentRuntime:
    config = Config(api_key="offline", model=provider.model, workspace=root)
    reviews = ReviewManager(root, mode=Mode.STANDARD, store=GrantStore(root / "grants.json"))
    return AgentRuntime(config, session, build_registry(root), provider, reviews)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="coderill-demo-") as directory:
        root = Path(directory)
        store = SessionStore(root / "sessions")
        target = root / "example.txt"
        target.write_text("original\n", encoding="utf-8")
        session = Session.new("offline-demo", str(root), store)
        provider = FakeProvider(
            ChatResponse("", (ToolCall("write-1", "write_file", {"path": "example.txt", "content": "agent\n"}),)),
            ChatResponse("The edit is complete."),
        )

        first = asyncio.run(runner(root, session, provider).run_turn("Update the file"))
        assert first.status == "waiting"
        print("1. Paused for review:", first.review["id"])

        target.write_text("human edit\n", encoding="utf-8")
        session = Session.load(session.id, store)
        runtime = runner(root, session, provider)
        stale = asyncio.run(runtime.resume_turn(first.review["id"], "approve"))
        assert stale.status == "waiting" and stale.review["id"] != first.review["id"]
        print("2. Old approval rejected; refreshed diff:")
        print(stale.review["diff"])

        finished = asyncio.run(runtime.resume_turn(stale.review["id"], "approve"))
        assert finished.status == "complete" and target.read_text(encoding="utf-8") == "agent\n"
        print("3. New approval applied; session events:", len(store.events(session.id)))

        crash = Session.new("offline-demo", str(root), store)
        crash.start_turn()
        crash.record({"role": "user", "content": "Write after a crash"})
        crash.record({"role": "assistant", "content": "", "tool_calls": [{
            "id": "write-2", "type": "function", "function": {
                "name": "write_file", "arguments": '{"path":"crash.txt","content":"unsafe"}',
            },
        }]})
        crash.start_tool({"id": "write-2", "name": "write_file", "arguments": {
            "path": "crash.txt", "content": "unsafe",
        }})
        recovered = asyncio.run(runner(root, Session.load(crash.id, store), FakeProvider(
            ChatResponse("I will inspect the actual file state first."),
        )).resume_turn())
        assert recovered.status == "complete" and not (root / "crash.txt").exists()
        print("4. Unfinished write marked unknown; no automatic replay.")


if __name__ == "__main__":
    main()
