import asyncio
import subprocess

from coderill.agent.subagents import SubagentService
from coderill.config import Config
from coderill.providers.types import ChatResponse, ChatStreamEvent, ToolCall
from coderill.session import Session, SessionStore


class Provider:
    def __init__(self):
        self.responses = [
            ChatResponse("", (ToolCall("c1", "write_file", {"path": "a.txt", "content": "child\n"}),)),
            ChatResponse("done"),
        ]

    async def stream(self, request):
        yield ChatStreamEvent("completed", response=self.responses.pop(0))


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def test_writable_child_stays_in_worktree_until_explicit_apply(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    git(project, "init")
    git(project, "config", "user.email", "test@example.com")
    git(project, "config", "user.name", "Test")
    target = project / "a.txt"
    target.write_text("parent\n")
    git(project, "add", "a.txt")
    git(project, "commit", "-m", "base")
    session = Session.new("fake", str(project), SessionStore(tmp_path / "sessions"))
    service = SubagentService(Config(api_key="unused", workspace=project), session, provider_factory=lambda _: Provider())

    async def scenario():
        report = await service.delegate("edit a.txt", allow_write=True)
        child_id = next(iter(session.subagents))
        assert "done" in report
        assert target.read_text() == "parent\n"
        assert (tmp_path / "worktrees" / child_id / "a.txt").read_text() == "child\n"
        diff = await service.preview_apply(child_id)
        assert "+child" in diff
        await service.apply(child_id, diff)
        assert target.read_text() == "child\n"
        assert Session.load(session.id, session.store).subagents[child_id]["applied"]
        await service.close()

    asyncio.run(scenario())
