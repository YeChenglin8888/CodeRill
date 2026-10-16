"""Optional Harbor installed-agent adapter for the current CodeRill checkout.

Harbor is intentionally not a CodeRill runtime dependency. Run this module with
Harbor installed in the evaluation environment, using the import path
``examples.harbor_agent:CodeRillHarborAgent``.
"""

from __future__ import annotations

import shlex
import shutil
import tempfile
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

REMOTE_SOURCE = "/installed-agent/coderill-src"
REMOTE_VENV = "/opt/coderill/.venv"


class CodeRillHarborAgent(BaseInstalledAgent):
    def __init__(self, *args, source_dir: str | Path | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.source_dir = Path(source_dir).resolve() if source_dir else Path(__file__).resolve().parents[1]

    @staticmethod
    def name() -> str:
        return "coderill"

    def get_version_command(self) -> str | None:
        return f"{REMOTE_VENV}/bin/python -m coderill --version"

    async def install(self, environment: BaseEnvironment) -> None:
        required = (self.source_dir / "pyproject.toml", self.source_dir / "README.md",
                    self.source_dir / "coderill")
        if any(not path.exists() for path in required):
            raise ValueError("CodeRill source_dir must contain pyproject.toml, README.md and coderill/")
        staged = Path(tempfile.mkdtemp(prefix="coderill-source-", dir=self.logs_dir))
        for name in ("pyproject.toml", "README.md"):
            shutil.copy2(self.source_dir / name, staged / name)
        shutil.copytree(self.source_dir / "coderill", staged / "coderill",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", ".env.*"))
        user = shlex.quote(str(environment.default_user or "root"))
        await self.exec_as_root(environment, command=(
            f"mkdir -p {REMOTE_SOURCE} /opt/coderill && chown -R {user} /opt/coderill {REMOTE_SOURCE}"
        ))
        await environment.upload_dir(staged, REMOTE_SOURCE)
        await self.exec_as_root(environment, command=f"chown -R {user} {REMOTE_SOURCE}")
        await self.exec_as_agent(environment, command=(
            f"python3 -m venv {REMOTE_VENV} && {REMOTE_VENV}/bin/python -m pip install {REMOTE_SOURCE}"
        ))

    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        del context
        await self.exec_as_agent(environment, command=(
            f"{REMOTE_VENV}/bin/python -m coderill --cwd . --auto-approve --benchmark "
            f"--message={shlex.quote(instruction)}"
        ))
