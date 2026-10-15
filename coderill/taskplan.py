"""A session task plan with revision-based concurrency.

The agent keeps a short plan of stable tasks and updates them by ID rather than
rewriting the whole list. Every write carries the revision the model last saw, so
a stale update is rejected instead of silently clobbering a newer plan — the
model must re-read with ``task_list`` and retry.

Two shapes are supported: ``linear`` (order is the plan order) and ``dag`` (order
comes from explicit ``depends_on`` edges). Derived state such as "ready" is
computed here, never submitted by the model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

STATUSES = ("pending", "in_progress", "done", "blocked")
MODES = ("linear", "dag")
_MARK = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]", "blocked": "[!]"}


class StaleRevision(Exception):
    """Raised when a write carries an out-of-date revision."""


class UnknownTask(Exception):
    """Raised when a task ID does not exist."""


@dataclass
class Task:
    id: str
    content: str
    status: str = "pending"
    owner: str | None = None
    depends_on: list[str] = field(default_factory=list)


class TaskPlan:
    """The plan itself. All mutations bump ``revision`` exactly once."""

    def __init__(self, mode: str = "linear") -> None:
        self.mode = mode if mode in MODES else "linear"
        self.revision = 0
        self.tasks: list[Task] = []

    # -- reads -------------------------------------------------------------

    def describe(self) -> str:
        header = f"task plan ({self.mode}, revision {self.revision})"
        body = self.render() or "(empty — create tasks with task_create)"
        ready = self.ready()
        hint = f"\nready: {', '.join(ready)}" if ready else ""
        return f"{header}\n{body}{hint}"

    def render(self) -> str:
        lines = []
        for task in self.tasks:
            tail = []
            if task.owner:
                tail.append(f"@{task.owner}")
            if task.depends_on:
                tail.append(f"after {', '.join(task.depends_on)}")
            suffix = f"  ({' '.join(tail)})" if tail else ""
            lines.append(f"{_MARK[task.status]} {task.id}  {task.content}{suffix}")
        return "\n".join(lines)

    def ready(self) -> list[str]:
        """IDs that are pending with every dependency done — derived, not stored."""
        done = {task.id for task in self.tasks if task.status == "done"}
        return [
            task.id
            for task in self.tasks
            if task.status == "pending" and all(dep in done for dep in task.depends_on)
        ]

    def unfinished(self) -> list[Task]:
        return [task for task in self.tasks if task.status != "done"]

    def snapshot(self) -> dict:
        return {"mode": self.mode, "revision": self.revision, "tasks": [asdict(t) for t in self.tasks]}

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> TaskPlan:
        plan = cls(snapshot["mode"])
        plan.revision = snapshot["revision"]
        plan.tasks = [Task(**task) for task in snapshot["tasks"]]
        return plan

    # -- writes ------------------------------------------------------------

    def create(self, contents: list[str], expected_revision: int, mode: str | None = None) -> str:
        self._require(expected_revision)
        if mode is not None and mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if not contents:
            raise ValueError("contents must be a non-empty list")
        for content in contents:
            if not isinstance(content, str) or not content.strip():
                raise ValueError("each task content must be a non-empty string")
        if mode is not None:
            self.mode = mode
        for content in contents:
            self.tasks.append(Task(id=self._next_id(), content=content.strip()))
        self._touch()
        return self.describe()

    def update(
        self,
        task_id: str,
        expected_revision: int,
        status: str | None = None,
        owner: str | None = None,
        depends_on: list[str] | None = None,
    ) -> str:
        self._require(expected_revision)
        task = self._get(task_id)
        if status is not None and status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        if depends_on is not None:
            for dep in depends_on:
                self._get(dep)
            graph = {item.id: (depends_on if item.id == task_id else item.depends_on) for item in self.tasks}
            visiting: set[str] = set()
            visited: set[str] = set()

            def visit(node: str) -> None:
                if node in visiting:
                    raise ValueError("task dependencies contain a cycle")
                if node in visited:
                    return
                visiting.add(node)
                for parent in graph[node]:
                    visit(parent)
                visiting.remove(node)
                visited.add(node)

            for node in graph:
                visit(node)
        if status is not None:
            task.status = status
        if owner is not None:
            task.owner = owner
        if depends_on is not None:
            task.depends_on = list(depends_on)
        self._touch()
        return self.describe()

    def revise(self, task_id: str, content: str, expected_revision: int) -> str:
        self._require(expected_revision)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")
        self._get(task_id).content = content.strip()
        self._touch()
        return self.describe()

    # -- internals ---------------------------------------------------------

    def _require(self, expected_revision: int) -> None:
        if expected_revision != self.revision:
            raise StaleRevision(
                f"stale revision: the plan is at {self.revision}, you passed {expected_revision}; "
                "call task_list and retry with the current revision"
            )

    def _get(self, task_id: str) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise UnknownTask(f"no task with id '{task_id}'")

    def _touch(self) -> None:
        self.revision += 1

    def _next_id(self) -> str:
        numbers = [int(task.id[1:]) for task in self.tasks if task.id[1:].isdigit()]
        return f"t{max(numbers, default=0) + 1}"
