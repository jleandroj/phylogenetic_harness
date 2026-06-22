"""Central audit log — so the operator can see EVERYTHING (runs, tool calls,
out-of-harness use) across the whole machine, not just one run.

Every run appends start/finish records here, and the shell guard
(scripts/harness-guard.sh) appends a record for every invocation of a registered
bio tool — including ones run OUTSIDE the harness, which are flagged. The format
is JSON-lines, append-only, flock-protected, so it is safe across processes and
auditable after the fact.

Default location: ``~/.harness/audit.jsonl`` (override with ``HARNESS_AUDIT_LOG``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover
    _HAVE_FCNTL = False


def audit_path() -> Path:
    p = os.environ.get("HARNESS_AUDIT_LOG")
    return Path(p) if p else Path.home() / ".harness" / "audit.jsonl"


def record(event: str, *, clock=None, **fields: Any) -> dict[str, Any]:
    """Append one audit record. ``clock`` is an injected ISO-timestamp callable;
    falls back to the real wall clock here (audit timestamps are not reproducible
    by design)."""
    from . import clock as _clock
    rec: dict[str, Any] = {
        "ts": (clock or _clock.iso_now)(),
        "event": event,
        "host": os.uname().nodename if hasattr(os, "uname") else None,
        "user": os.environ.get("USER"),
        "pid": os.getpid(),
        "harness_run_id": os.environ.get("HARNESS_RUN_ID"),  # None => outside a harness run
    }
    rec.update(fields)
    path = audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, sort_keys=True, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        if _HAVE_FCNTL:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
        finally:
            if _HAVE_FCNTL:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return rec


def read(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else audit_path()
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def summary(path: str | Path | None = None) -> dict[str, Any]:
    """Operator-facing summary of the audit log."""
    recs = read(path)
    runs = {r.get("run_id") for r in recs if r.get("event") == "run_started"}
    finished = {r.get("run_id") for r in recs if r.get("event") == "run_finished"}
    tool_calls = [r for r in recs if r.get("event") == "tool_call"]
    out_of_harness = [r for r in tool_calls if not r.get("harness_run_id")]
    failures = [r for r in recs if r.get("event") in ("run_failed", "task_failed_audit")]
    return {
        "audit_log": str(audit_path() if not path else path),
        "total_records": len(recs),
        "runs_started": len([r for r in recs if r.get("event") == "run_started"]),
        "runs_unfinished": sorted(runs - finished),
        "tool_calls": len(tool_calls),
        "tool_calls_OUTSIDE_harness": len(out_of_harness),
        "out_of_harness_examples": [
            {"tool": r.get("tool"), "ts": r.get("ts"), "cwd": r.get("cwd")}
            for r in out_of_harness[-10:]
        ],
        "failures": len(failures),
    }
