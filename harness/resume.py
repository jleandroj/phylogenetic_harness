"""Resume a crashed run from disk (audit P1.6).

Loads the frozen RunConfig + task plan, rehydrates technical state from the event
log (``recovery.rebuild_state``), records any orphaned tasks (LEASED/RUNNING with
no terminal event — their worker died), and re-runs only the unfinished tasks via
the fault-isolating Supervisor (which also quarantines crash-loop poison tasks).
Tasks already terminal are skipped.

This is what lets a run survive a 2am crash: nothing is lost, no zombie remains,
and only the remaining work runs.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from . import recovery, taskstore
from .events import EventStore, EventType
from .supervisor import Supervisor


def resume_run(run_dir: str | Path, *, tools_dir: str | Path | None = None) -> dict[str, Any]:
    from .manifest import DEFAULT_TOOLS_DIR
    from .run import Run, RunConfig

    run_dir = Path(run_dir)
    cfg_dict = json.loads((run_dir / "RUN_CONFIG.json").read_text(encoding="utf-8"))
    valid = set(RunConfig.__dataclass_fields__)
    cfg = RunConfig(**{k: v for k, v in cfg_dict.items() if k in valid})
    # Resume re-produces outputs of interrupted tasks; allow overwrite for those.
    cfg = dataclasses.replace(cfg, allow_overwrite=True, output_dir=str(run_dir))

    # Rehydrate + record orphans BEFORE re-running (audit P0.3/P1.6).
    pre_events = EventStore(run_dir / "events" / "run.events.jsonl")
    state = recovery.rebuild_state(pre_events)
    orphans = [s.task_id for s in recovery.find_orphans(state)]
    pre_events.emit(EventType.RECOVERY_STARTED, orphans=orphans)
    for tid in orphans:
        pre_events.emit(EventType.TASK_REAPED, task_id=tid, via="resume")

    run = Run(cfg, base_dir=run_dir.parent)
    run.load_tools(tools_dir or DEFAULT_TOOLS_DIR)
    tasks = taskstore.load_tasks(run_dir)
    # Supervisor over Scheduler: a task that hard-crashed the process on a prior
    # attempt is quarantined here, so resume can never enter an infinite crash loop.
    # graceful_shutdown traps SIGTERM/SIGINT so a stop mid-grid releases leases and
    # records the interruption instead of leaving zombies.
    from .signals import graceful_shutdown
    with graceful_shutdown(run_dir):
        summary = Supervisor(run.build_runner(worker_id="resume"), run_dir).run(tasks, resume=True)
    run.events.emit(EventType.RECOVERY_COMPLETED, resumed=summary["total"] - len(summary["skipped"]))
    run.finish()

    # After resume, no task may remain an orphan.
    post_state = recovery.rebuild_state(EventStore(run_dir / "events" / "run.events.jsonl"))
    summary["orphans_before"] = orphans
    summary["orphans_after"] = [s.task_id for s in recovery.find_orphans(post_state)]
    return summary
