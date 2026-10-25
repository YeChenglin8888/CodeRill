"""Sandbox policy regressions that do not require a Docker daemon."""

import asyncio
import os

import pytest

from coderill.agent.runtime import AgentRuntime
from coderill.config import Config
from coderill.permissions import Mode, ReviewManager
from coderill.providers.types import ChatResponse, ChatStreamEvent, ToolCall
from coderill.runtime.process import ProcessResult
from coderill.sandbox import DockerJob, SandboxError, SandboxPolicy, _squid_config
from coderill.session import Session, SessionStore
from coderill.tools import build_registry


def test_project_config_and_dotenv_cannot_enable_or_relax_sandbox(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".coderill.toml").write_text('sandbox = "off"\nsandbox_network = "none"\n')
    (project / ".env").write_text("CODERILL_SANDBOX=off\nCODERILL_SANDBOX_NETWORK=none\n")
    monkeypatch.setenv("CODERILL_API_KEY", "unused")
    monkeypatch.setenv("CODERILL_SANDBOX", "docker")
    config = Config.from_env(project)
    assert config.sandbox == "docker"
    assert config.sandbox_network == "restricted"


def test_denied_file_blocks_read_and_parent_mutation(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    config = Config(api_key="unused", workspace=tmp_path, sandbox="docker", deny_read_paths=("secret.txt",))
    registry = build_registry(tmp_path, config=config)
    assert "denies reading" in registry.dispatch("read_file", {"path": "secret.txt"})
    assert "denies changing a parent" in registry.dispatch("delete_path", {"path": ".", "recursive": True})


def test_denied_hard_link_is_rejected(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    os.link(secret, tmp_path / "alias.txt")
    with pytest.raises(ValueError, match="hard links"):
        SandboxPolicy(tmp_path, backend="docker", deny_read_paths=("secret.txt",))


def test_search_does_not_follow_file_symlink_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret-outside")
    try:
        (workspace / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    registry = build_registry(workspace)
    assert "secret-outside" not in registry.dispatch("grep", {"pattern": "secret-outside"})
    assert "link.txt" not in registry.dispatch("glob", {"pattern": "*.txt"})


def test_read_only_profile_refuses_host_file_tools(tmp_path):
    config = Config(api_key="unused", workspace=tmp_path, sandbox="docker", sandbox_profile="read-only")
    registry = build_registry(tmp_path, config=config)
    assert "read-only" in registry.dispatch("write_file", {"path": "new.txt", "content": "x"})
    assert not (tmp_path / "new.txt").exists()


def test_unavailable_docker_never_retries_a_shell_command_on_host(monkeypatch, tmp_path):
    config = Config(api_key="unused", workspace=tmp_path, sandbox="docker")
    registry = build_registry(tmp_path, config=config)

    def unavailable(*_args, **_kwargs):
        raise SandboxError("Docker engine unavailable")

    monkeypatch.setattr("coderill.sandbox.docker", unavailable)
    result = registry.dispatch("bash", {"command": "echo leaked > host.txt"})
    assert "Docker engine unavailable" in result
    assert not (tmp_path / "host.txt").exists()


def test_extra_network_domain_requires_review_even_when_tool_approval_is_bypassed(tmp_path):
    config = _squid_config(("pypi.org:443",))
    assert "acl CONNECT method CONNECT" in config
    assert config.index("http_access deny !allowed") < config.index("http_access deny private")
    assert "http_access deny private" in config
    assert "http_access allow allowed" in config
    policy = SandboxPolicy(tmp_path, backend="docker")
    with pytest.raises(SandboxError, match="granted"):
        DockerJob(policy, extra_domains=("example.com:443",))

    class Provider:
        def __init__(self):
            self.responses = [ChatResponse("", (ToolCall("c", "bash", {
                "command": "echo ok", "network_domains": ["example.com:443"],
            }),)), ChatResponse("done")]

        async def stream(self, _request):
            yield ChatStreamEvent("completed", response=self.responses.pop(0))

    session = Session.new("fake", str(tmp_path), SessionStore(tmp_path / "sessions"))
    config = Config(api_key="unused", model="fake", workspace=tmp_path, sandbox="docker")
    runner = AgentRuntime(config, session, build_registry(tmp_path, config=config), Provider(),
                          ReviewManager(tmp_path, mode=Mode.BYPASS, policy=config.sandbox_policy))
    events = []
    runner.on_event = events.append

    async def fake_run(*_args):
        return ProcessResult(0, "ok", "success")

    runner.processes.run = fake_run
    first = asyncio.run(runner.run_turn("run"))
    assert first.status == "waiting" and first.review["kind"] == "sandbox_network"
    assert first.review["domains"] == ["example.com:443"]
    second = asyncio.run(runner.resume_turn(first.review["id"], "approve"))
    assert second.status == "complete"
    assert session.finished_calls["c"]["status"] == "success"
    assert not runner._network_once
    assert any(event.kind == "tool_started" and event.status == "docker" for event in events)
