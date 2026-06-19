"""Worker tracking (spec §15).

Workers register, heartbeat, and can be declared lost. Time is supplied as a
monotonically increasing tick (injected) so recovery logic is deterministic and
testable without sleeping.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .events import EventStore, EventType


@dataclass
class Worker:
    worker_id: str
    last_heartbeat: float
    alive: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "last_heartbeat": self.last_heartbeat,
            "alive": self.alive,
        }


class WorkerManager:
    def __init__(self, *, events: EventStore | None = None, heartbeat_timeout: float = 30.0) -> None:
        self.events = events
        self.heartbeat_timeout = heartbeat_timeout
        self._workers: dict[str, Worker] = {}

    def start(self, worker_id: str, now: float) -> Worker:
        w = Worker(worker_id=worker_id, last_heartbeat=now)
        self._workers[worker_id] = w
        if self.events:
            self.events.emit(EventType.WORKER_STARTED, worker_id=worker_id, at=now)
        return w

    def heartbeat(self, worker_id: str, now: float) -> None:
        w = self._workers.get(worker_id)
        if w is None:
            raise KeyError(f"unknown worker {worker_id!r}")
        w.last_heartbeat = now
        w.alive = True
        if self.events:
            self.events.emit(EventType.WORKER_HEARTBEAT, worker_id=worker_id, at=now)

    def detect_lost(self, now: float) -> list[Worker]:
        """Mark workers whose heartbeat aged past the timeout as lost (spec §15)."""
        lost: list[Worker] = []
        for w in self._workers.values():
            if w.alive and (now - w.last_heartbeat) > self.heartbeat_timeout:
                w.alive = False
                lost.append(w)
                if self.events:
                    self.events.emit(
                        EventType.WORKER_LOST,
                        worker_id=w.worker_id,
                        last_heartbeat=w.last_heartbeat,
                        now=now,
                    )
        return lost

    def alive_workers(self) -> list[str]:
        return [wid for wid, w in self._workers.items() if w.alive]
