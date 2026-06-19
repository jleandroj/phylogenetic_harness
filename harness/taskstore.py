"""Persist the task plan so a run can be resumed/replayed (audit P1.6/P1.7/Q1).

Tasks are written to ``runs/{run}/TASKS.jsonl`` (one task per line). This is the
authoritative plan: the scheduler, ``resume`` and ``replay`` all reconstruct
Task objects from here, so a crashed run can be continued from disk alone.
"""
from __future__ import annotations

import json
from pathlib import Path

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
