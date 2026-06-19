"""Approval gate (spec §16, §24.12).

A task with ``requires_approval=True`` does not run unless an approval has been
granted. The gate also auto-flags tasks that should require approval based on
resource thresholds (e.g. >50% of host RAM, GPU-intensive, very long walltime),
so an unmarked dangerous task is caught rather than silently executed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .events import EventStore, EventType
from .tasks import Task


@dataclass
class Approval:
    task_id: str
    granted: bool
    approved_by: str | None = None
    approved_at: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "granted": self.granted,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "reason": self.reason,
        }


class ApprovalError(Exception):
    """Raised when a task that requires approval is run without it."""


class ApprovalGate:
    def __init__(
        self,
        *,
        policy: str = "strict",
        host_memory_gb: float | None = None,
        events: EventStore | None = None,
        ram_fraction_threshold: float = 0.5,
        walltime_minutes_threshold: int = 12 * 60,
    ) -> None:
        self.policy = policy
        self.host_memory_gb = host_memory_gb
        self.events = events
        self.ram_fraction_threshold = ram_fraction_threshold
        self.walltime_minutes_threshold = walltime_minutes_threshold
        self._approvals: dict[str, Approval] = {}

    def auto_flags(self, task: Task) -> list[str]:
        """Reasons this task should require approval, independent of its flag."""
        reasons: list[str] = []
        if task.resources.gpu:
            reasons.append("uses GPU")
        if (
            self.host_memory_gb
            and task.resources.memory_gb > self.ram_fraction_threshold * self.host_memory_gb
        ):
            reasons.append(
                f"requests {task.resources.memory_gb} GB "
                f"(> {int(self.ram_fraction_threshold * 100)}% of host RAM)"
            )
        if task.resources.walltime_minutes > self.walltime_minutes_threshold:
            reasons.append(f"walltime {task.resources.walltime_minutes} min exceeds threshold")
        if "overwrite" in task.params and task.params["overwrite"]:
            reasons.append("overwrites existing outputs")
        return reasons

    def needs_approval(self, task: Task) -> bool:
        return bool(task.requires_approval or self.auto_flags(task))

    def grant(self, approval: Approval) -> None:
        self._approvals[approval.task_id] = approval
        if self.events:
            etype = EventType.APPROVAL_GRANTED if approval.granted else EventType.APPROVAL_DENIED
            self.events.emit(etype, **approval.to_dict())

    def is_granted(self, task_id: str) -> bool:
        a = self._approvals.get(task_id)
        return bool(a and a.granted)

    def check(self, task: Task) -> None:
        """Raise ApprovalError if the task needs approval and lacks a grant."""
        flags = self.auto_flags(task)
        required = task.requires_approval or bool(flags)
        if not required:
            return
        if self.events:
            self.events.emit(
                EventType.APPROVAL_REQUIRED,
                task_id=task.task_id,
                declared=task.requires_approval,
                auto_flags=flags,
            )
        if not self.is_granted(task.task_id):
            raise ApprovalError(
                f"task {task.task_id!r} requires approval and none was granted "
                f"(declared={task.requires_approval}, auto_flags={flags})"
            )
