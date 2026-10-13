"""Optional whole-task browser tool; Playwright is imported only when executing."""

from typing import ClassVar

from ..browser import run_task
from .base import Tool


class BrowserTaskTool(Tool):
    name = "browser_task"
    description = (
        "Delegate a complete HTTP(S) browser task to Jev using a dedicated visible browser profile. "
        "Return JSON page evidence and a stop reason. model_done is a model claim: independently verify the goal. "
        "If execution_uncertain is returned, verify the current page state before retrying; do not replay blindly. "
        "Requires consent for the whole task. Main agent only; no frames, uploads, downloads, new tabs, or CAPTCHA solving."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Starting HTTP(S) URL without embedded credentials"},
            "goal": {"type": "string", "description": "Complete task including all necessary facts and intended outcome"},
            "max_steps": {"type": "integer", "minimum": 1, "maximum": 60, "default": 60},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300, "default": 120,
                                "description": "Cooperative deadline; in-flight waits and cleanup may finish later"},
        },
        "required": ["url", "goal"],
        "additionalProperties": False,
    }

    def __init__(self, llm, config):
        self.llm, self.config = llm, config

    def execute(self, url: str, goal: str, max_steps: int = 60, timeout_seconds: int = 120) -> str:
        return run_task(url, goal, max_steps, timeout_seconds, self.llm, self.config)
