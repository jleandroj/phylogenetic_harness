"""State reconstruction and orphan detection (spec §15, §24.11; audit P0.3).

The event store is the source of truth. ``rebuild_state`` folds the ordered event
log into the current technical state of every task, so after a crash a new
process can reconstruct exactly where each task was — no in-memory state needed.

A task left in LEASED or RUNNING with no terminal event is an ORPHAN: its worker
died mid-flight. ``find_orphans`` surfaces them so the reaper can requeue them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .events import EventStore
from .states import TechnicalState

# Event name -> resulting technical state. Mirrors what TaskRunner/LeaseManager emit.
_EVENT_STATE: dict[str, TechnicalState] = {
    "task_created": TechnicalState.PENDING,
    "task_approved": TechnicalState.APPROVED,
    "task_leased": TechnicalState.LEASED,
    "task_started": TechnicalState.RUNNING,
    "task_succeeded": TechnicalState.SUCCEEDED,
    "task_reaped": TechnicalState.EXPIRED,
    "task_requeued": TechnicalState.REQUEUED,
}

_ORPHAN_STATES = {TechnicalState.LEASED, TechnicalState.RUNNING}


@dataclass
class TaskState:
    task_id: str
    status_technical: TechnicalState = TechnicalState.PENDING
    retries: int = 0
    lease_worker: str | None = None
    last_event_seq: int = 0
    failed: bool = False
    history: list[str] = field(default_factory=list)

    @property
    def is_orphan(self) -> bool:
        return self.status_technical in _ORPHAN_STATES

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status_technical": self.status_technical.value,
            "retries": self.retries,
            "lease_worker": self.lease_worker,
            "is_orphan": self.is_orphan,
            "failed": self.failed,
            "last_event_seq": self.last_event_seq,
        }


def rebuild_state(events: EventStore) -> dict[str, TaskState]:
    """Reconstruct per-task technical state from the ordered event log."""
    states: dict[str, TaskState] = {}
    for ev in sorted(events.read(), key=lambda e: e.get("seq", 0)):
        task_id = ev.get("task_id")
        if not task_id:
            continue
        st = states.setdefault(task_id, TaskState(task_id=task_id))
        st.last_event_seq = ev.get("seq", st.last_event_seq)
        name = ev.get("event", "")
        st.history.append(name)

        if name == "task_failed":
            st.status_technical = TechnicalState.FAILED_FATAL
            st.failed = True
        elif name in _EVENT_STATE:
            st.status_technical = _EVENT_STATE[name]
            if name == "task_leased":
                st.lease_worker = ev.get("worker_id") or ev.get("worker")
            elif name == "task_requeued":
                st.retries = ev.get("attempt", st.retries)
                st.lease_worker = None
            elif name == "task_succeeded":
                st.lease_worker = None
    return states


def find_orphans(states: dict[str, TaskState]) -> list[TaskState]:
    """Tasks stuck in LEASED/RUNNING — their worker died without a terminal event."""
    return [s for s in states.values() if s.is_orphan]
