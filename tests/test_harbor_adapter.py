"""Harbor adapter contract without installing Harbor or starting Docker."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def test_harbor_adapter_stages_only_package_and_quotes_instruction(tmp_path, monkeypatch):
    commands = []

    class Base:
        def __init__(self, *, logs_dir):
            self.logs_dir = logs_dir

        async def exec_as_root(self, _environment, *, command):
            commands.append(("root", command))

        async def exec_as_agent(self, _environment, *, command):
            commands.append(("agent", command))

    modules = {name: ModuleType(name) for name in (
        "harbor", "harbor.agents", "harbor.agents.installed", "harbor.agents.installed.base",
        "harbor.environments", "harbor.environments.base", "harbor.models", "harbor.models.agent",
        "harbor.models.agent.context",
    )}
    modules["harbor.agents.installed.base"].BaseInstalledAgent = Base
    modules["harbor.agents.installed.base"].with_prompt_template = lambda function: function
    modules["harbor.environments.base"].BaseEnvironment = object
    modules["harbor.models.agent.context"].AgentContext = object
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location(
        "coderill_harbor_contract", Path(__file__).resolve().parents[1] / "examples" / "harbor_agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source = tmp_path / "source"
    (source / "coderill").mkdir(parents=True)
    (source / "pyproject.toml").write_text("[project]\nname='coderill'", encoding="utf-8")
    (source / "README.md").write_text("CodeRill", encoding="utf-8")
    (source / "coderill" / "__init__.py").write_text("", encoding="utf-8")
    (source / "coderill" / ".env").write_text("secret", encoding="utf-8")
    (source / ".env").write_text("secret", encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()

    class Environment:
        default_user = "agent"

        async def upload_dir(self, staged, remote):
            assert remote == module.REMOTE_SOURCE
            assert sorted(path.name for path in staged.iterdir()) == ["README.md", "coderill", "pyproject.toml"]
            assert not (staged / ".env").exists()
            assert not (staged / "coderill" / ".env").exists()

    agent = module.CodeRillHarborAgent(logs_dir=logs, source_dir=source)
    environment = Environment()
    asyncio.run(agent.install(environment))
    asyncio.run(agent.run("-fix 'quoted' path", environment, None))
    assert any("pip install --target /opt/coderill/packages /installed-agent/coderill-src" in command
               for _, command in commands)
    assert "PYTHONPATH=/opt/coderill/packages python3 -m coderill" in commands[-1][1]
    assert "--message='-fix '\"'\"'quoted'\"'\"' path'" in commands[-1][1]
    assert agent.name() == "coderill"
