"""Iter 9 (round 2): graceful shutdown on signals (robustness guarantee #5).

If the harness process is asked to stop — operator Ctrl-C (SIGINT), a service
manager / shutdown (SIGTERM), or the OOM-killer reaching the harness — it must not
die mid-task leaving leased tasks, orphaned child tools, and no record.

On a trapped signal we:
  1. set the per-run kill-switch STOP marker, so no NEW task starts (the runner's
     containment gate already refuses to launch when STOP is present);
  2. record ``run_interrupted`` to the central audit log (best-effort);
  3. raise KeyboardInterrupt, so the runner's try/finally blocks release the lease
     and the executor's process-group kill reaps any running child tool.

The original handlers are restored on exit, so this composes cleanly and is
testable without killing the test process.
"""
from __future__ import annotations

import signal
from pathlib import Path
from typing import Any


def graceful_stop(run_dir: str | Path, *, signum: int | None = None) -> dict[str, Any]:
    """Idempotent cleanup: arm the kill-switch + record the interruption."""
    from . import audit, killswitch
    p = killswitch.stop(run_dir, reason=f"graceful shutdown (signal {signum})")
    rec = None
    try:
        rec = audit.record("run_interrupted", run_dir=str(run_dir), signal=signum)
    except audit.AuditUnavailable:
        pass
    return {"stop_marker": str(p), "audited": rec is not None}


class graceful_shutdown:
    """Context manager: trap SIGTERM/SIGINT for the duration of a run.

    Usage::

        with graceful_shutdown(run.dir):
            scheduler.run(tasks)
    """

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self._prev: dict[int, Any] = {}

    def _handler(self, signum: int, _frame: Any) -> None:
        graceful_stop(self.run_dir, signum=signum)
        raise KeyboardInterrupt(f"harness interrupted by signal {signum}")

    def __enter__(self) -> graceful_shutdown:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._prev[sig] = signal.signal(sig, self._handler)
            except (ValueError, OSError):  # e.g. not in main thread
                pass
        return self

    def __exit__(self, *exc: Any) -> None:
        for sig, prev in self._prev.items():
            try:
                signal.signal(sig, prev)
            except (ValueError, OSError):
                pass
