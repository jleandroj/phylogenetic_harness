"""Resource monitoring (spec §24.9).

Measures CPU time, peak RAM and disk-free deltas around a child process. If a
measurement could not be obtained, the result is explicitly flagged
``RESOURCE_AUDIT_INCOMPLETE`` rather than silently reported as zero — an
intensive task with unmeasured resources is audit debt, not a clean run.
"""
from __future__ import annotations

import os
import resource
import shutil
from dataclasses import dataclass, field
from typing import Any


def _free_bytes(path: str | os.PathLike[str]) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


@dataclass
class ResourceUsage:
    cpu_user_s: float | None = None
    cpu_system_s: float | None = None
    max_rss_mb: float | None = None
    disk_free_before_bytes: int | None = None
    disk_free_after_bytes: int | None = None
    wall_seconds: float | None = None
    incomplete_reasons: list[str] = field(default_factory=list)

    @property
    def audit_complete(self) -> bool:
        return not self.incomplete_reasons

    @property
    def status(self) -> str:
        return "RESOURCE_AUDIT_OK" if self.audit_complete else "RESOURCE_AUDIT_INCOMPLETE"

    @property
    def disk_written_bytes(self) -> int | None:
        if self.disk_free_before_bytes is None or self.disk_free_after_bytes is None:
            return None
        # Free space dropping => bytes written (best-effort, shared filesystem noise).
        return max(0, self.disk_free_before_bytes - self.disk_free_after_bytes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_user_s": self.cpu_user_s,
            "cpu_system_s": self.cpu_system_s,
            "max_rss_mb": self.max_rss_mb,
            "disk_free_before_bytes": self.disk_free_before_bytes,
            "disk_free_after_bytes": self.disk_free_after_bytes,
            "disk_written_bytes": self.disk_written_bytes,
            "wall_seconds": self.wall_seconds,
            "status": self.status,
            "audit_complete": self.audit_complete,
            "incomplete_reasons": list(self.incomplete_reasons),
        }


class ChildResourceProbe:
    """Snapshots child-process resource counters before/after an execution.

    Uses ``RUSAGE_CHILDREN`` deltas (cumulative across all reaped children) plus
    disk-free deltas. ``wall_seconds`` is supplied by the caller (the executor
    times the command) so this module never calls a forbidden implicit clock.
    """

    def __init__(self, disk_path: str | os.PathLike[str] = ".") -> None:
        self.disk_path = disk_path
        self._before: resource.struct_rusage | None = None
        self._disk_before: int | None = None

    def start(self) -> None:
        try:
            self._before = resource.getrusage(resource.RUSAGE_CHILDREN)
        except (OSError, ValueError):
            self._before = None
        self._disk_before = _free_bytes(self.disk_path)

    def stop(self, wall_seconds: float | None = None) -> ResourceUsage:
        usage = ResourceUsage(wall_seconds=wall_seconds)
        try:
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
        except (OSError, ValueError):
            after = None
        if self._before is None or after is None:
            usage.incomplete_reasons.append("getrusage(RUSAGE_CHILDREN) unavailable")
        else:
            usage.cpu_user_s = max(0.0, after.ru_utime - self._before.ru_utime)
            usage.cpu_system_s = max(0.0, after.ru_stime - self._before.ru_stime)
            # ru_maxrss is kB on Linux, bytes on macOS. Assume Linux kB (target host).
            usage.max_rss_mb = round(after.ru_maxrss / 1024, 2)
        usage.disk_free_before_bytes = self._disk_before
        usage.disk_free_after_bytes = _free_bytes(self.disk_path)
        if usage.disk_free_before_bytes is None or usage.disk_free_after_bytes is None:
            usage.incomplete_reasons.append("disk free unavailable")
        if wall_seconds is None:
            usage.incomplete_reasons.append("wall time not provided")
        return usage
