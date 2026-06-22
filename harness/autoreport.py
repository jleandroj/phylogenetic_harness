"""Automatic run summary + anomaly detection (production guarantee #2).

On finish (or failure) the harness writes, for a run:
  RUN_SUMMARY.json  — what happened (tasks, outcomes, scientific verdicts).
  ANOMALIES.json    — anything wrong/anomalous the harness detected.
  ALERT.txt         — a one-line operator-facing alert.

Anomalies are detected sceptically (assume the agent/tools can misbehave):
failed/degenerate tasks, timeouts, validation failures, sandbox-off, orphaned
tasks (a worker died mid-run), out-of-harness tool calls during the run, and a
broken audit hash chain (tamper).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import audit, recovery
from .events import EventStore


def _bundles(run_dir: Path) -> list[dict[str, Any]]:
    out = []
    for f in sorted((run_dir / "results").glob("*.validation.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def detect_anomalies(run_dir: str | Path, run_id: str | None = None) -> list[dict[str, Any]]:
    run_dir = Path(run_dir)
    anomalies: list[dict[str, Any]] = []
    bundles = _bundles(run_dir)

    for b in bundles:
        tid = b.get("task_id")
        if str(b.get("status_technical", "")).startswith("FAILED"):
            anomalies.append({"kind": "task_failed", "task_id": tid,
                              "state": b.get("status_technical")})
        if b.get("degenerate"):
            anomalies.append({"kind": "degenerate_output", "task_id": tid})
        ex = b.get("execution") or {}
        if ex.get("timed_out"):
            anomalies.append({"kind": "timeout", "task_id": tid})
        if ex.get("disk_aborted"):
            anomalies.append({"kind": "disk_abort", "task_id": tid})
        if not b.get("validators_passed", True) and b.get("status_technical") == "SUCCEEDED":
            anomalies.append({"kind": "validation_failed", "task_id": tid})

    # Orphaned tasks (a worker died mid-run -> LEASED/RUNNING with no terminal).
    evpath = run_dir / "events" / "run.events.jsonl"
    if evpath.exists():
        state = recovery.rebuild_state(EventStore(evpath))
        for orphan in recovery.find_orphans(state):
            anomalies.append({"kind": "orphan_task", "task_id": orphan.task_id})

    # Out-of-harness tool calls recorded during this run, and a broken audit chain.
    if run_id:
        for r in audit.read():
            if (r.get("event") == "tool_call" and not r.get("harness_run_id")):
                anomalies.append({"kind": "out_of_harness_tool", "tool": r.get("tool"),
                                  "ts": r.get("ts")})
    chain = audit.verify()
    if not chain.get("ok"):
        anomalies.append({"kind": "audit_chain_broken", "broken_at": chain.get("broken_at")})

    return anomalies


def run_summary(run_dir: str | Path, run_id: str | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    bundles = _bundles(run_dir)
    sci: dict[str, int] = {}
    for b in bundles:
        s = b.get("status_scientific")
        if s:
            sci[s] = sci.get(s, 0) + 1
    succeeded = sum(1 for b in bundles if b.get("status_technical") == "SUCCEEDED")
    failed = sum(1 for b in bundles if str(b.get("status_technical", "")).startswith("FAILED"))
    return {
        "run_id": run_id,
        "tasks": len(bundles),
        "succeeded": succeeded,
        "failed": failed,
        "degenerate": sum(1 for b in bundles if b.get("degenerate")),
        "scientific_verdicts": sci,
    }


def generate(run_dir: str | Path, run_id: str | None = None) -> dict[str, Any]:
    """Write RUN_SUMMARY.json + ANOMALIES.json + ALERT.txt. Returns a small dict."""
    run_dir = Path(run_dir)
    summary = run_summary(run_dir, run_id)
    anomalies = detect_anomalies(run_dir, run_id)
    (run_dir / "RUN_SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True),
                                              encoding="utf-8")
    (run_dir / "ANOMALIES.json").write_text(json.dumps(anomalies, indent=2, sort_keys=True),
                                            encoding="utf-8")
    if anomalies:
        kinds = sorted({a["kind"] for a in anomalies})
        alert = (f"⚠ ALERT run {run_id}: {len(anomalies)} anomaly(ies) detected: {kinds}. "
                 f"{summary['failed']} failed / {summary['tasks']} tasks.")
    else:
        alert = (f"✓ run {run_id} clean: {summary['succeeded']}/{summary['tasks']} tasks "
                 f"succeeded, no anomalies.")
    (run_dir / "ALERT.txt").write_text(alert + "\n", encoding="utf-8")
    return {"summary": summary, "anomalies": anomalies, "alert": alert,
            "n_anomalies": len(anomalies)}
