"""Append-only event store (spec §8).

Every meaningful state change in a run is recorded as an immutable event in a
JSON-lines file. The set of event types is a closed enum so that an unknown
event name is a programming error, not a silent typo in an audit trail.
"""
from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import Any


class EventType(str, Enum):
    # Run / environment lifecycle
    RUN_CREATED = "run_created"
    ENVIRONMENT_CAPTURED = "environment_captured"
    TOOL_DETECTED = "tool_detected"
    INPUT_REGISTERED = "input_registered"
    INPUT_VALIDATED = "input_validated"
    # Task lifecycle
    TASK_CREATED = "task_created"
    TASK_APPROVED = "task_approved"
    TASK_LEASED = "task_leased"
    TASK_STARTED = "task_started"
    COMMAND_STARTED = "command_started"
    COMMAND_STDOUT_CAPTURED = "command_stdout_captured"
    COMMAND_STDERR_CAPTURED = "command_stderr_captured"
    COMMAND_FINISHED = "command_finished"
    TASK_SUCCEEDED = "task_succeeded"
    TASK_FAILED = "task_failed"
    # Validation
    VALIDATION_STARTED = "validation_started"
    VALIDATION_SUCCEEDED = "validation_succeeded"
    VALIDATION_FAILED = "validation_failed"
    # Scientific verdicts
    RESULT_MARKED_DEGENERATE = "result_marked_degenerate"
    RESULT_MARKED_NEGATIVE = "result_marked_negative"
    RESULT_MARKED_INCONCLUSIVE = "result_marked_inconclusive"
    RESULT_INTERPRETATION_LIMITED = "result_interpretation_limited"
    # Workers / leases / recovery
    WORKER_STARTED = "worker_started"
    WORKER_HEARTBEAT = "worker_heartbeat"
    WORKER_LOST = "worker_lost"
    LEASE_EXPIRED = "lease_expired"
    TASK_REQUEUED = "task_requeued"
    TASK_REAPED = "task_reaped"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_COMPLETED = "recovery_completed"
    # Resources
    GPU_ASSIGNED = "gpu_assigned"
    GPU_MEMORY_MEASURED = "gpu_memory_measured"
    CPU_MEMORY_MEASURED = "cpu_memory_measured"
    DISK_USAGE_MEASURED = "disk_usage_measured"
    # Approval
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    # Hooks (RESERVED, audit P3.13): there is no hook subsystem yet; these names
    # are reserved so the vocabulary is stable when one lands. They are never
    # emitted today — do not rely on them.
    HOOK_FIRED = "hook_fired"
    HOOK_SUCCEEDED = "hook_succeeded"
    HOOK_FAILED = "hook_failed"
    # Reporting
    REPORT_GENERATED = "report_generated"
    RUN_FINISHED = "run_finished"


try:
    import fcntl  # POSIX advisory locks (audit P0.4)
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - non-POSIX
    _HAVE_FCNTL = False


class EventStore:
    """Append-only JSONL event store, safe for MULTIPLE PROCESSES (audit P0.4).

    Every append takes an exclusive ``flock`` on the file and assigns ``seq`` by
    counting existing lines under that lock, so two workers writing the same file
    never interleave a partial line nor collide on a sequence number. ``worker``
    is recorded on each event when provided, giving a total order of
    ``(seq, ts, worker)``.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        clock: callable | None = None,
        worker: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # intra-process; flock covers inter-process
        self._clock = clock
        self.worker = worker

    def emit(self, event_type: EventType | str, **fields: Any) -> dict[str, Any]:
        if isinstance(event_type, EventType):
            etype = event_type.value
        else:
            # Enforce closed vocabulary: raises ValueError on unknown name.
            etype = EventType(event_type).value
        with self._lock:
            # 'a+b' so the fd is positioned at EOF for the append; we still take an
            # exclusive flock to serialise across processes and to count lines for seq.
            with open(self.path, "a+b") as fh:
                if _HAVE_FCNTL:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    fh.seek(0)
                    seq = sum(1 for _ in fh) + 1  # global across all writers
                    event: dict[str, Any] = {"seq": seq, "event": etype}
                    if self._clock is not None:
                        event["ts"] = self._clock()
                    if self.worker is not None:
                        event["worker"] = self.worker
                    event.update(fields)
                    line = (json.dumps(event, sort_keys=True, default=str) + "\n").encode("utf-8")
                    fh.seek(0, os.SEEK_END)
                    fh.write(line)
                    fh.flush()
                    os.fsync(fh.fileno())
                finally:
                    if _HAVE_FCNTL:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return event

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def iter_type(self, event_type: EventType) -> Iterator[dict[str, Any]]:
        for ev in self.read():
            if ev.get("event") == event_type.value:
                yield ev
