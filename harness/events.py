"""Append-only event store (spec §8).

Every meaningful state change in a run is recorded as an immutable event in a
JSON-lines file. The set of event types is a closed enum so that an unknown
event name is a programming error, not a silent typo in an audit trail.
"""
from __future__ import annotations

import json
import os
import threading
from enum import Enum
from pathlib import Path
from typing import Any, Iterator


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
    # Hooks / reporting
    HOOK_FIRED = "hook_fired"
    HOOK_SUCCEEDED = "hook_succeeded"
    HOOK_FAILED = "hook_failed"
    REPORT_GENERATED = "report_generated"
    RUN_FINISHED = "run_finished"


class EventStore:
    """Append-only JSONL event store. One file per store; thread-safe."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        clock: "callable | None" = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._clock = clock
        self._seq = 0

    def emit(self, event_type: EventType | str, **fields: Any) -> dict[str, Any]:
        if isinstance(event_type, EventType):
            etype = event_type.value
        else:
            # Enforce closed vocabulary: raises ValueError on unknown name.
            etype = EventType(event_type).value
        with self._lock:
            self._seq += 1
            event: dict[str, Any] = {"seq": self._seq, "event": etype}
            if self._clock is not None:
                event["ts"] = self._clock()
            event.update(fields)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True, default=str) + "\n")
            return event

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def iter_type(self, event_type: EventType) -> Iterator[dict[str, Any]]:
        for ev in self.read():
            if ev.get("event") == event_type.value:
                yield ev
