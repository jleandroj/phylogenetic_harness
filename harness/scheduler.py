"""Minimal fault-isolating scheduler (audit P1.7).

Runs a LIST of tasks through the single TaskRunner. A task that fails (or even
raises — though the runner already contains exceptions) does NOT stop the others:
each task is independent and its outcome is recorded. Progress is persisted (the
plan in TASKS.jsonl + per-task bundles), so the run is resumable: on resume,
tasks already in a terminal state are skipped and only the unfinished ones run.

This is what answers "a cell of the grid fails at 2am": the failed cell is
recorded, the rest complete, and `resume` finishes whatever was left.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import taskstore
from .events import EventType
from .runner import TaskRunner
from .tasks import Task


class Scheduler:
    def __init__(self, runner: TaskRunner, run_dir: str | Path) -> None:
        self.runner = runner
        self.run_dir = Path(run_dir)

    def run(
        self, tasks: list[Task], *, resume: bool = False, run_kwargs: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Execute tasks, isolating failures. Returns a summary.

        ``run_kwargs`` optionally maps task_id -> kwargs forwarded to run_task
        (e.g. validator_kwargs, statistical_checks).
        """
        run_kwargs = run_kwargs or {}
        if not resume:
            taskstore.save_tasks(self.run_dir, tasks)

        summary: dict[str, str] = {}
        skipped: list[str] = []
        for task in tasks:
            if resume and taskstore.is_done(self.run_dir, task.task_id):
                skipped.append(task.task_id)
                summary[task.task_id] = taskstore.bundle_state(self.run_dir, task.task_id) or "DONE"
                continue
            try:
                bundle = self.runner.run_task(task, **run_kwargs.get(task.task_id, {}))
                summary[task.task_id] = bundle["status_technical"]
            except Exception as exc:  # defensive: never let one cell stop the grid
                # The runner contains its own exceptions; this is belt-and-suspenders
                # for a programming error in the runner itself.
                self.runner.events.emit(
                    EventType.TASK_FAILED, task_id=task.task_id,
                    reason=f"scheduler_caught:{type(exc).__name__}:{exc}", state="FAILED_FATAL",
                )
                summary[task.task_id] = "FAILED_FATAL"

        succeeded = sum(1 for s in summary.values() if s == "SUCCEEDED")
        failed = sum(1 for s in summary.values() if s.startswith("FAILED"))
        return {
            "total": len(tasks),
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "by_task": summary,
        }
