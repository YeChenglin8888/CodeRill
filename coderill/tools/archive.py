"""Read source material removed from the short model context."""

from __future__ import annotations

from typing import ClassVar

from ..session import Session
from .base import Tool, ToolError


class ArchiveReadTool(Tool):
    name = "archive_read"
    description = "Retrieve original text archived during context compression by archive id."
    read_only = True
    truncate_output = False
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "archive_id": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "required": ["archive_id"],
    }

    def __init__(self, session: Session) -> None:
        self.session = session

    def execute(self, archive_id: str, offset: int = 0, limit: int = 20_000) -> str:
        try:
            content = self.session.read_archive(archive_id)
        except (OSError, ValueError) as exc:
            raise ToolError(f"archive {archive_id} is unavailable") from exc
        if offset < 0 or limit < 1 or limit > 25_000:
            raise ToolError("offset must be nonnegative and limit must be 1–25000")
        return f"{content[offset:offset + limit]}\n[characters {offset}-{min(offset + limit, len(content))} of {len(content)}]"
