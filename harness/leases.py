"""Lease management and recovery / reaping (spec §15, §24.11).

A RUNNING task holds a lease with an expiry tick. If the holding worker dies, the
lease expires; the reaper then transitions the task EXPIRED -> REQUEUED so it can
never remain a zombie. Recovery emits explicit events
(lease_expired / task_reaped / task_requeued) and respects the task's retry
budget: a task out of retries goes to FAILED_FATAL instead of looping forever.

Time is an injected monotonic tick so this is deterministic and testable without
real waiting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .events import EventStore, EventType
from .states import TechnicalState
from .tasks import Task


@dataclass
class Lease:
    task_id: str
    worker_id: str
    granted_at: float
    expires_at: float

    def expired(self, now: float) -> bool:
        return now >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
        }


class LeaseManager:
    def __init__(self, *, events: EventStore | None = None, lease_seconds: float = 60.0) -> None:
        self.events = events
        self.lease_seconds = lease_seconds
        self._leases: dict[str, Lease] = {}

    def acquire(self, task: Task, worker_id: str, now: float) -> Lease:
        """Lease an APPROVED/REQUEUED task to a worker and mark it LEASED."""
        if task.status_technical not in (TechnicalState.APPROVED, TechnicalState.REQUEUED):
            raise ValueError(
                f"task {task.task_id} not leasable from {task.status_technical.value}"
            )
        if task.status_technical == TechnicalState.REQUEUED:
            task.set_technical(TechnicalState.APPROVED)
        task.set_technical(TechnicalState.LEASED)
        lease = Lease(task.task_id, worker_id, now, now + self.lease_seconds)
        self._leases[task.task_id] = lease
        if self.events:
            self.events.emit(EventType.TASK_LEASED, **lease.to_dict())
        return lease

    def renew(self, task_id: str, now: float) -> None:
        lease = self._leases.get(task_id)
        if lease:
            lease.expires_at = now + self.lease_seconds

    def release(self, task_id: str) -> None:
        self._leases.pop(task_id, None)

    def reap_expired(self, tasks: dict[str, Task], now: float) -> list[str]:
        """Reap tasks whose lease expired while RUNNING/LEASED (spec §24.11).

        Returns the list of requeued task ids. A task that has exhausted retries
        is taken to FAILED_FATAL rather than requeued, so recovery converges.
        """
        if self.events:
            self.events.emit(EventType.RECOVERY_STARTED, at=now)
        requeued: list[str] = []
        for task_id, lease in list(self._leases.items()):
            if not lease.expired(now):
                continue
            task = tasks.get(task_id)
            if task is None:
                self._leases.pop(task_id, None)
                continue
            if task.status_technical not in (TechnicalState.LEASED, TechnicalState.RUNNING):
                self._leases.pop(task_id, None)
                continue

            self.events and self.events.emit(
                EventType.LEASE_EXPIRED, **lease.to_dict(), now=now
            )
            self.events and self.events.emit(
                EventType.WORKER_LOST, worker_id=lease.worker_id, via="lease_expiry"
            )
            # RUNNING/LEASED -> EXPIRED (declared legal transition).
            task.set_technical(TechnicalState.EXPIRED)
            self.events and self.events.emit(EventType.TASK_REAPED, task_id=task_id)

            if task.retries < task.failure_policy.max_retries:
                task.retries += 1
                task.set_technical(TechnicalState.REQUEUED)
                self.events and self.events.emit(
                    EventType.TASK_REQUEUED, task_id=task_id, attempt=task.retries
                )
                requeued.append(task_id)
            else:
                task.set_technical(TechnicalState.FAILED_FATAL)
                self.events and self.events.emit(
                    EventType.TASK_FAILED, task_id=task_id, reason="retries_exhausted_after_expiry"
                )
            self._leases.pop(task_id, None)

        if self.events:
            self.events.emit(EventType.RECOVERY_COMPLETED, at=now, requeued=len(requeued))
        return requeued
