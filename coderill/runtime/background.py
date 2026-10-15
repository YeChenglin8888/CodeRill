"""In-process background jobs with durable status and one-time notifications."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from ..session import Session


class BackgroundTaskManager:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._running: dict[str, asyncio.Task] = {}

    def start(self, description: str, child_session_id: str, work: Callable[[], Awaitable[str]],
              *, plan_task_id: str | None = None, plan_revision: int | None = None) -> str:
        task_id = uuid.uuid4().hex[:12]
        self.session.start_background({
            "id": task_id, "description": description, "child_session_id": child_session_id,
            "status": "running", "result": "", "notified": False,
            "plan_task_id": plan_task_id, "plan_revision": plan_revision,
        })
        self._running[task_id] = asyncio.create_task(self._drive(task_id, work))
        return task_id

    async def _drive(self, task_id: str, work: Callable[[], Awaitable[str]]) -> None:
        try:
            result = await work()
        except asyncio.CancelledError:
            self.session.finish_background(task_id, "interrupted", "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - background failures are durable results
            self.session.finish_background(task_id, "failed", str(exc))
        else:
            self.session.finish_background(task_id, "complete", result)
        finally:
            self._running.pop(task_id, None)

    def status(self, task_id: str) -> dict:
        return dict(self.session.background_tasks[task_id])

    def cancel(self, task_id: str) -> None:
        task = self._running.get(task_id)
        if task is None:
            raise ValueError("background task is not running in this process")
        task.cancel()

    def notifications(self, *, inject: bool = False) -> list[dict]:
        completed = []
        for task_id, task in self.session.background_tasks.items():
            if task["status"] != "running" and not task.get("notified"):
                completed.append(dict(task))
                message = f"[Background task {task_id} {task['status']}]\n{task['result']}" if inject else None
                self.session.notify_background(task_id, message)
        return completed

    async def close(self) -> None:
        tasks = list(self._running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
