"""Resource monitoring (spec §24.9, audit P1.6).

Measures CPU time, peak RAM and disk-free deltas around a child process. Peak
RAM is measured PER CHILD PID by sampling ``/proc/<pid>/status`` (VmRSS/VmHWM)
in a background thread, so concurrent tasks in the same parent do not pollute
each other's numbers. ``RUSAGE_CHILDREN`` remains a fallback, explicitly flagged
incomplete because it is cumulative across all children.

If a measurement could not be obtained the result is flagged
``RESOURCE_AUDIT_INCOMPLETE`` rather than silently reported as zero.
"""
from __future__ import annotations

import os
import resource
import shutil
import threading
from dataclasses import dataclass, field
from typing import Any


def _free_bytes(path: str | os.PathLike[str]) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


class PidSampler(threading.Thread):
    """Samples peak RSS of a single PID via /proc (Linux). Daemon thread.

    Tracks the max of sampled ``VmRSS`` and the kernel's ``VmHWM`` high-water
    mark for that exact PID — never the whole process group.
    """

    def __init__(self, pid: int, interval: float = 0.05) -> None:
        super().__init__(daemon=True)
        self.pid = pid
        self.interval = interval
        self.max_rss_kb = 0
        self.samples = 0
        self._stop_evt = threading.Event()

    def _read_status_kb(self) -> tuple[int | None, int | None]:
        rss = hwm = None
        try:
            with open(f"/proc/{self.pid}/status", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1])
                    elif line.startswith("VmHWM:"):
                        hwm = int(line.split()[1])
        except (OSError, ValueError):
            return None, None
        return rss, hwm

    def run(self) -> None:
        while not self._stop_evt.is_set():
            rss, hwm = self._read_status_kb()
            for v in (rss, hwm):
                if v:
                    self.max_rss_kb = max(self.max_rss_kb, v)
                    self.samples += 1
            self._stop_evt.wait(self.interval)
        # final read in case the peak happened just before exit
        _, hwm = self._read_status_kb()
        if hwm:
            self.max_rss_kb = max(self.max_rss_kb, hwm)

    def stop(self) -> None:
        self._stop_evt.set()


@dataclass
class ResourceUsage:
    cpu_user_s: float | None = None
    cpu_system_s: float | None = None
    max_rss_mb: float | None = None
    rss_source: str = "unmeasured"  # per_pid | rusage_children_fallback | unmeasured
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
        return max(0, self.disk_free_before_bytes - self.disk_free_after_bytes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_user_s": self.cpu_user_s,
            "cpu_system_s": self.cpu_system_s,
            "max_rss_mb": self.max_rss_mb,
            "rss_source": self.rss_source,
            "disk_free_before_bytes": self.disk_free_before_bytes,
            "disk_free_after_bytes": self.disk_free_after_bytes,
            "disk_written_bytes": self.disk_written_bytes,
            "wall_seconds": self.wall_seconds,
            "status": self.status,
            "audit_complete": self.audit_complete,
            "incomplete_reasons": list(self.incomplete_reasons),
        }


# ru_maxrss is kB on Linux, bytes on macOS (P3.13: detect platform).
_RUSAGE_MAXRSS_DIVISOR = 1024 if os.uname().sysname != "Darwin" else 1024 * 1024


class ChildResourceProbe:
    """Snapshots resource counters around a single child execution.

    ``max_rss_mb`` comes from a per-PID sampler when a pid is provided; otherwise
    it falls back to ``RUSAGE_CHILDREN`` (flagged incomplete). CPU time comes from
    ``RUSAGE_CHILDREN`` deltas; ``wall_seconds`` is supplied by the executor.
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

    def stop(
        self,
        wall_seconds: float | None = None,
        *,
        sampler: PidSampler | None = None,
    ) -> ResourceUsage:
        usage = ResourceUsage(wall_seconds=wall_seconds)
        try:
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
        except (OSError, ValueError):
            after = None

        if self._before is not None and after is not None:
            usage.cpu_user_s = max(0.0, after.ru_utime - self._before.ru_utime)
            usage.cpu_system_s = max(0.0, after.ru_stime - self._before.ru_stime)
        else:
            usage.incomplete_reasons.append("getrusage(RUSAGE_CHILDREN) unavailable")

        # Peak RSS: prefer the per-PID sampler (concurrency-safe).
        if sampler is not None and sampler.max_rss_kb > 0:
            usage.max_rss_mb = round(sampler.max_rss_kb / 1024, 2)
            usage.rss_source = "per_pid"
        elif after is not None:
            usage.max_rss_mb = round(after.ru_maxrss / _RUSAGE_MAXRSS_DIVISOR, 2)
            usage.rss_source = "rusage_children_fallback"
            usage.incomplete_reasons.append("peak RSS from cumulative RUSAGE_CHILDREN, not per-PID")
        else:
            usage.incomplete_reasons.append("peak RSS unmeasured")

        usage.disk_free_before_bytes = self._disk_before
        usage.disk_free_after_bytes = _free_bytes(self.disk_path)
        if usage.disk_free_before_bytes is None or usage.disk_free_after_bytes is None:
            usage.incomplete_reasons.append("disk free unavailable")
        if wall_seconds is None:
            usage.incomplete_reasons.append("wall time not provided")
        return usage
