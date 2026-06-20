"""Persist the task plan so a run can be resumed/replayed (audit P1.6/P1.7/Q1).

Tasks are written to ``runs/{run}/TASKS.jsonl`` (one task per line). This is the
authoritative plan: the scheduler, ``resume`` and ``replay`` all reconstruct
Task objects from here, so a crashed run can be continued from disk alone.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .tasks import Task

PLAN_NAME = "TASKS.jsonl"


def save_tasks(run_dir: str | Path, tasks: list[Task]) -> Path:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / PLAN_NAME
    with open(path, "w", encoding="utf-8") as fh:
        for t in tasks:
            fh.write(json.dumps(t.to_dict(), sort_keys=True) + "\n")
    return path


def load_tasks(run_dir: str | Path) -> list[Task]:
    path = Path(run_dir) / PLAN_NAME
    if not path.exists():
        return []
    tasks: list[Task] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tasks.append(Task.from_dict(json.loads(line)))
    return tasks


# Technical states that mean "do not re-run on resume" (terminal).
TERMINAL_DONE = {"SUCCEEDED", "FAILED_FATAL", "CANCELLED"}


def bundle_state(run_dir: str | Path, task_id: str) -> str | None:
    """Return the persisted terminal state of a task, or None if it never finished."""
    bundle = Path(run_dir) / "results" / f"{task_id}.validation.json"
    if not bundle.exists():
        return None
    try:
        return json.loads(bundle.read_text(encoding="utf-8")).get("status_technical")
    except (OSError, json.JSONDecodeError):
        return None


def is_done(run_dir: str | Path, task_id: str) -> bool:
    return bundle_state(run_dir, task_id) in TERMINAL_DONE


def append_task(run_dir: str | Path, task: Task) -> None:
    """Append a task to the plan if not already recorded (idempotent)."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    if task.task_id in {t.task_id for t in load_tasks(run_dir)}:
        return
    with open(run_dir / PLAN_NAME, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(task.to_dict(), sort_keys=True) + "\n")


try:
    import fcntl  # POSIX advisory locks
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover
    _HAVE_FCNTL = False


def _resumed_bundle(run_dir: Path, task_id: str) -> dict[str, Any] | None:
    """Return a prior SUCCEEDED bundle iff its outputs still exist, else None."""
    if bundle_state(run_dir, task_id) != "SUCCEEDED":
        return None
    bf = run_dir / "results" / f"{task_id}.validation.json"
    try:
        bundle = json.loads(bf.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if bundle and all(Path(o["path"]).exists() for o in bundle.get("outputs", []) if o.get("path")):
        return bundle
    return None


def run_or_resume(runner: Any, task: Task, **run_kwargs: Any) -> dict[str, Any]:
    """Run a task through the runner, OR resume: if a prior SUCCEEDED bundle exists
    and its outputs are still on disk, reuse it without re-executing.

    Cross-process safe (audit round 4): an exclusive per-task ``flock`` serialises
    concurrent runners on the same run-id, so two ``harness pipeline --run-id X``
    never execute the same task twice — the second blocks, then sees the first's
    SUCCEEDED bundle and resumes.
    """
    run_dir = Path(runner.results_dir).parent
    append_task(run_dir, task)  # record the plan before running (audit/recovery)

    # Fast path: already done.
    done = _resumed_bundle(run_dir, task.task_id)
    if done is not None:
        return done

    lock_dir = run_dir / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{task.task_id}.lock"
    with open(lock_path, "w", encoding="utf-8") as lock_fh:
        if _HAVE_FCNTL:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)  # blocks a concurrent runner
        try:
            # Re-check under the lock: a peer may have completed it while we waited.
            done = _resumed_bundle(run_dir, task.task_id)
            if done is not None:
                return done
            # (Re-)execute from a CLEAN technical state — Task.from_dict resets
            # status/retries, so a reused/reconstructed task never trips the guard.
            return runner.run_task(Task.from_dict(task.to_dict()), **run_kwargs)
        finally:
            if _HAVE_FCNTL:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
