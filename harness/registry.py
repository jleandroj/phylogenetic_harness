"""Run registry — catalogue every run the operator has executed.

Built from the central audit log (run_started/run_finished) enriched with each
run's on-disk outcome (technical + scientific verdicts from the result bundles),
so the operator can answer "what runs exist, did they finish, and what did they
conclude" across the whole machine.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import audit


def _run_outcome(run_dir: Path) -> dict[str, Any]:
    results = run_dir / "results"
    n = succeeded = failed = degenerate = 0
    sci: dict[str, int] = {}
    if results.is_dir():
        for f in results.glob("*.validation.json"):
            try:
                b = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            n += 1
            t = b.get("status_technical")
            if t == "SUCCEEDED":
                succeeded += 1
            elif str(t).startswith("FAILED"):
                failed += 1
            if b.get("degenerate"):
                degenerate += 1
            s = b.get("status_scientific")
            if s:
                sci[s] = sci.get(s, 0) + 1
    return {"tasks": n, "succeeded": succeeded, "failed": failed,
            "degenerate": degenerate, "scientific": sci}


def list_runs() -> list[dict[str, Any]]:
    recs = audit.read()
    started = {r["run_id"]: r for r in recs if r.get("event") == "run_started" and r.get("run_id")}
    finished = {r["run_id"] for r in recs if r.get("event") == "run_finished"}
    runs = []
    for run_id, r in started.items():
        rd = Path(r.get("run_dir", ""))
        entry = {
            "run_id": run_id,
            "started_at": r.get("ts"),
            "finished": run_id in finished,
            "mode": r.get("mode"),
            "sandbox": r.get("sandbox"),
            "strict": r.get("strict"),
            "run_dir": str(rd),
            "exists": rd.exists(),
        }
        if rd.exists():
            entry.update(_run_outcome(rd))
        runs.append(entry)
    return sorted(runs, key=lambda e: e.get("started_at") or "")
