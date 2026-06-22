"""Iter 9: robustness — crash-loop containment over the scheduler (guarantee #5).

The scheduler already isolates *exceptions*: one task that fails or raises does
not stop the grid. But a task can do worse than raise — it can take down the
whole harness PROCESS (a tool that segfaults the interpreter, an OOM-kill of the
worker, a power loss mid-task). On ``resume`` the same poison task runs again,
crashes again: an infinite crash loop that blocks every remaining task forever.

The Supervisor closes that hole with two cheap, persistent mechanisms:

  * **Heartbeat / in-flight marker** — before each task it writes
    ``INFLIGHT.json`` (the task currently executing) and clears it on completion.
    If the process dies mid-task, the marker survives and names the culprit.
  * **Strike ledger + quarantine** — on start, a surviving in-flight marker is a
    crash signature: that task gets a strike in ``STRIKES.json``. A task with
    ``>= max_strikes`` strikes is QUARANTINED — skipped, audited, and recorded as
    a terminal POISONED bundle — so the crash loop is broken and the rest of the
    grid completes.

An agent failure — even one that kills the process — can therefore never take
down the harness or starve the other work.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import audit, taskstore
from .events import EventType
from .runner import TaskRunner
from .tasks import Task


class Supervisor:
    def __init__(self, runner: TaskRunner, run_dir: str | Path, *, max_strikes: int = 2) -> None:
        self.runner = runner
        self.run_dir = Path(run_dir)
        self.max_strikes = max_strikes
        self._inflight = self.run_dir / "INFLIGHT.json"
        self._strikes_path = self.run_dir / "STRIKES.json"

    # --- strike ledger -----------------------------------------------------
    def _load_strikes(self) -> dict[str, int]:
        if self._strikes_path.exists():
            try:
                return json.loads(self._strikes_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _save_strikes(self, strikes: dict[str, int]) -> None:
        tmp = self._strikes_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(strikes, sort_keys=True), encoding="utf-8")
        tmp.replace(self._strikes_path)

    def _reconcile_crash(self, strikes: dict[str, int]) -> str | None:
        """A surviving in-flight marker means the process died mid-task last run."""
        if not self._inflight.exists():
            return None
        try:
            tid = json.loads(self._inflight.read_text(encoding="utf-8")).get("task_id")
        except (OSError, json.JSONDecodeError):
            tid = None
        self._inflight.unlink(missing_ok=True)
        if tid:
            strikes[tid] = strikes.get(tid, 0) + 1
            audit.record("task_crash_detected", task_id=tid, strikes=strikes[tid],
                         run_dir=str(self.run_dir))
        return tid

    def _mark_inflight(self, task: Task) -> None:
        self._inflight.write_text(json.dumps({"task_id": task.task_id}), encoding="utf-8")

    def _clear_inflight(self) -> None:
        self._inflight.unlink(missing_ok=True)

    def _quarantine(self, task: Task, strikes: int) -> dict[str, Any]:
        reason = f"quarantined after {strikes} crash strike(s) (poison task)"
        self.runner.events.emit(EventType.TASK_FAILED, task_id=task.task_id,
                                reason=reason, state="FAILED_FATAL")
        audit.record("task_quarantined", task_id=task.task_id, strikes=strikes,
                     run_dir=str(self.run_dir))
        bundle = {"task_id": task.task_id, "task_type": task.task_type, "tool_id": task.tool_id,
                  "status_technical": "FAILED_FATAL", "status_scientific": "NOT_EVALUATED",
                  "poisoned": True, "quarantine_reason": reason, "degenerate": False,
                  "validators_passed": False, "retries": 0, "outputs": [],
                  "execution": None, "validation": [], "interpretation": {}}
        self.runner._persist_bundle(task.task_id, bundle)
        return bundle

    # --- main loop ---------------------------------------------------------
    def run(self, tasks: list[Task], *, resume: bool = False,
            run_kwargs: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        run_kwargs = run_kwargs or {}
        if not resume:
            taskstore.save_tasks(self.run_dir, tasks)

        strikes = self._load_strikes()
        crashed = self._reconcile_crash(strikes)   # blame the task that was in flight
        self._save_strikes(strikes)

        summary: dict[str, str] = {}
        skipped: list[str] = []
        quarantined: list[str] = []
        for task in tasks:
            if resume and taskstore.is_done(self.run_dir, task.task_id):
                skipped.append(task.task_id)
                summary[task.task_id] = taskstore.bundle_state(self.run_dir, task.task_id) or "DONE"
                continue
            if strikes.get(task.task_id, 0) >= self.max_strikes:
                self._quarantine(task, strikes[task.task_id])
                quarantined.append(task.task_id)
                summary[task.task_id] = "QUARANTINED"
                continue
            self._mark_inflight(task)
            try:
                bundle = self.runner.run_task(task, **run_kwargs.get(task.task_id, {}))
                summary[task.task_id] = bundle["status_technical"]
            except Exception as exc:  # never let one cell stop the grid
                self.runner.events.emit(EventType.TASK_FAILED, task_id=task.task_id,
                                        reason=f"supervisor_caught:{type(exc).__name__}:{exc}",
                                        state="FAILED_FATAL")
                summary[task.task_id] = "FAILED_FATAL"
            finally:
                self._clear_inflight()

        return {
            "total": len(tasks),
            "succeeded": sum(1 for s in summary.values() if s == "SUCCEEDED"),
            "failed": sum(1 for s in summary.values() if s.startswith("FAILED")),
            "quarantined": quarantined,
            "crashed_last_run": crashed,
            "skipped": skipped,
            "by_task": summary,
        }
