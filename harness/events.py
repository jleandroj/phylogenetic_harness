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
    # Hooks (audit round 4 #1): emitted by harness.hooks.HookRegistry when a
    # lifecycle hook fires/succeeds/fails (pre_task / post_task / on_error).
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
        fsync_every: int = 1,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq_path = self.path.with_suffix(self.path.suffix + ".seq")
        self._lock = threading.Lock()  # intra-process; flock covers inter-process
        self._clock = clock
        self.worker = worker
        # fsync_every=1 -> durable (fsync each emit); larger batches trade crash
        # durability for throughput (audit P1.5). 0 disables fsync (flush only).
        self.fsync_every = fsync_every
        self._since_fsync = 0

    def _next_seq(self) -> int:
        """Global sequence via a sidecar counter, guarded by the same flock as the
        append. O(1) per emit instead of re-counting the whole file (audit P1.5)."""
        try:
            cur = int(self._seq_path.read_text())
        except (OSError, ValueError):
            cur = 0
        nxt = cur + 1
        tmp = self._seq_path.with_suffix(self._seq_path.suffix + ".tmp")
        tmp.write_text(str(nxt))
        os.replace(tmp, self._seq_path)
        return nxt

    def emit(self, event_type: EventType | str, **fields: Any) -> dict[str, Any]:
        if isinstance(event_type, EventType):
            etype = event_type.value
        else:
            # Enforce closed vocabulary: raises ValueError on unknown name.
            etype = EventType(event_type).value
        with self._lock:
            with open(self.path, "ab") as fh:
                if _HAVE_FCNTL:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    seq = self._next_seq()  # O(1), under the exclusive lock
                    event: dict[str, Any] = {"seq": seq, "event": etype}
                    if self._clock is not None:
                        event["ts"] = self._clock()
                    if self.worker is not None:
                        event["worker"] = self.worker
                    event.update(fields)
                    line = (json.dumps(event, sort_keys=True, default=str) + "\n").encode("utf-8")
                    fh.write(line)
                    fh.flush()  # always visible to other processes via the page cache
                    self._since_fsync += 1
                    if self.fsync_every and self._since_fsync >= self.fsync_every:
                        os.fsync(fh.fileno())
                        self._since_fsync = 0
                finally:
                    if _HAVE_FCNTL:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return event

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        # Shared lock so a concurrent append cannot expose a half-written line (P2.9).
        with open(self.path, encoding="utf-8") as fh:
            if _HAVE_FCNTL:
                fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
            try:
                for line in fh:
                    line = line.strip()
                    if line:
                        out.append(json.loads(line))
            finally:
                if _HAVE_FCNTL:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return out

    def iter_type(self, event_type: EventType) -> Iterator[dict[str, Any]]:
        for ev in self.read():
            if ev.get("event") == event_type.value:
                yield ev
