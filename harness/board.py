"""Task board: the single source of truth for what has been done and what must run next.

Every unit of delegated work is a Task with dependencies. The board is saved
after every state change, so one mechanism covers four situations:

- retry          a failed task goes back to `pending`
- audit re-run   `invalidate` re-opens the blamed task and everything downstream
- change request same as an audit re-run, with the user's feedback attached
- crash/resume   reload the file; finished tasks stay finished
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Task:
    id: str
    kind: str  # research | plan | prd | spec | implement | audit | adhoc
    agent: str
    deps: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | running | done | failed
    attempts: int = 0  # attempts since the task was last (re)opened
    runs: int = 0  # executions over the task's whole life
    version: int = 0  # successful completions; lets the auditor skip unchanged work
    feedback: list[str] = field(default_factory=list)  # why it is being run again
    result: dict | None = None
    error: str | None = None
    seconds: float = 0.0
    tokens: int = 0
    meta: dict = field(default_factory=dict)


class Board:
    def __init__(self, path: Path):
        self.path = path
        self.tasks: dict[str, Task] = {}
        self.meta: dict = {}
        if path.exists():
            saved = json.loads(path.read_text())
            self.meta = saved["meta"]
            self.tasks = {t["id"]: Task(**t) for t in saved["tasks"]}
            for task in self.tasks.values():  # a task still `running` means the process died mid-task
                if task.status == "running":  # the attempt never finished, so it does not count against the budget
                    task.status, task.attempts = (
                        ("failed" if task.kind == "adhoc" else "pending"),
                        max(task.attempts - 1, 0),
                    )

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"meta": self.meta, "tasks": [asdict(t) for t in self.tasks.values()]}, indent=1, default=str)
        )
        os.replace(tmp, self.path)  # atomic: a crash never leaves a half-written board

    def add(self, task: Task) -> Task:
        self.tasks[task.id] = task
        return task

    def drop(self, kind: str) -> None:
        self.tasks = {tid: t for tid, t in self.tasks.items() if t.kind != kind}

    def pipeline(self) -> list[Task]:
        """The delivery steps, in order (everything except the audit and ad-hoc questions)."""
        return [t for t in self.tasks.values() if t.kind not in ("audit", "adhoc")]

    def ready(self) -> Task | None:
        """The next task to run: pending, with every dependency done."""
        for task in self.tasks.values():
            if (
                task.status == "pending"
                and task.kind != "adhoc"
                and all(self.tasks[d].status == "done" for d in task.deps)
            ):
                return task
        return None

    def dependents(self, task_id: str) -> set[str]:
        """Every task that directly or transitively depends on task_id."""
        found: set[str] = set()
        frontier = {task_id}
        while frontier:
            frontier = {t.id for t in self.tasks.values() if frontier & set(t.deps)} - found
            found |= frontier
        return found

    def invalidate(self, task_id: str, feedback: list[str]) -> None:
        """Re-open a task with feedback, and everything downstream of it."""
        self.tasks[task_id].feedback += feedback
        for tid in {task_id} | self.dependents(task_id):
            task = self.tasks[tid]
            note = f"Upstream task '{task_id}' was revised; update your work so it stays consistent."
            if tid != task_id and task.result is not None and note not in task.feedback:
                task.feedback.append(note)
            task.status, task.attempts, task.error = "pending", 0, None
        self.save()
