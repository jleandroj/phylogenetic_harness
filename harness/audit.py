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


class AuditUnavailable(Exception):
    """Raised when the audit log cannot be written. The harness is fail-closed:
    if an action cannot be logged, it must not run (guarantee #1)."""


def audit_path() -> Path:
    p = os.environ.get("HARNESS_AUDIT_LOG")
    return Path(p) if p else Path.home() / ".harness" / "audit.jsonl"


def ensure_writable() -> Path:
    """Confirm the audit log is appendable+durable, or raise AuditUnavailable.

    Called BEFORE any task executes: a bad-faith agent that fills the disk or
    removes write permission on the log must not be able to run un-logged work.
    """
    path = audit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        raise AuditUnavailable(f"audit log not writable at {path}: {exc}") from exc
    return path


AUDIT_KEY_ENV = "HARNESS_AUDIT_KEY"


def _chain_mac(line: bytes) -> str:
    """Chain link over one record line.

    If ``HARNESS_AUDIT_KEY`` is set, this is a keyed HMAC-SHA256: an agent that can
    write the log still cannot recompute a valid chain without the operator's key
    (tamper-PROOF). The executor strips that key from every child tool's
    environment, so a malicious tool cannot read it. With no key set it degrades to
    a plain sha256 chain (tamper-EVIDENT), preserving backward compatibility.
    """
    import hashlib
    import hmac
    key = os.environ.get(AUDIT_KEY_ENV)
    if key:
        return "hmac:" + hmac.new(key.encode("utf-8"), line, hashlib.sha256).hexdigest()
    return hashlib.sha256(line).hexdigest()


def _last_hash(path: Path) -> str:
    """Chain MAC of the last record line (the chain head), or GENESIS if empty."""
    if not path.exists() or path.stat().st_size == 0:
        return "GENESIS"
    last = b""
    with open(path, "rb") as fh:
        for line in fh:
            if line.strip():
                last = line
    return _chain_mac(last)


def record(event: str, *, clock=None, **fields: Any) -> dict[str, Any]:
    """Append one TAMPER-EVIDENT audit record (append-only, hash-chained).

    Each record carries ``prev`` = sha256 of the previous record line, so any
    deletion or edit breaks the chain (``verify()`` detects it). The whole append
    is flock-guarded so it is safe across processes. ``clock`` injects an ISO
    timestamp (audit timestamps are not reproducible by design)."""
    from . import clock as _clock
    path = audit_path()
    try:
        return _record_locked(path, event, clock, fields, _clock)
    except OSError as exc:
        raise AuditUnavailable(f"could not append audit record to {path}: {exc}") from exc


def _record_locked(path, event, clock, fields, _clock) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh:
        if _HAVE_FCNTL:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            rec: dict[str, Any] = {
                "ts": (clock or _clock.iso_now)(),
                "event": event,
                "host": os.uname().nodename if hasattr(os, "uname") else None,
                "user": os.environ.get("USER"),
                "pid": os.getpid(),
                "harness_run_id": os.environ.get("HARNESS_RUN_ID"),  # None => outside a run
                "prev": _last_hash(path),  # hash chain -> tamper-evident
            }
            rec.update(fields)
            fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())  # durability: the record survives a crash/power loss
        finally:
            if _HAVE_FCNTL:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return rec


def verify(path: str | Path | None = None) -> dict[str, Any]:
    """Verify the hash chain: returns {ok, broken_at}. A broken chain means the
    log was edited/truncated out of band (tamper detected)."""
    p = Path(path) if path else audit_path()
    keyed = bool(os.environ.get(AUDIT_KEY_ENV))
    if not p.exists():
        return {"ok": True, "records": 0, "broken_at": None, "keyed": keyed}
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    expected = "GENESIS"
    for i, ln in enumerate(lines):
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            return {"ok": False, "records": len(lines), "broken_at": i, "reason": "unparseable"}
        if rec.get("prev") != expected:
            return {"ok": False, "records": len(lines), "broken_at": i,
                    "reason": "prev mismatch", "keyed": keyed}
        expected = _chain_mac((ln + "\n").encode("utf-8"))
    return {"ok": True, "records": len(lines), "broken_at": None, "keyed": keyed}


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
