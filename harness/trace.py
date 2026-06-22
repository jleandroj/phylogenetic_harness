"""Iter 8: traceability (production guarantee #4).

Every execution has a unique run_id. ``trace(run_id)`` reconstructs *exactly* what
happened by merging the three independent records the harness keeps:

  * the central **audit log** (machine-wide: run_started/finished, tool_call,
    action_finished, action_blocked, kill/panic) — the tamper-evident spine;
  * the per-run **event store** (``runs/{run}/events/run.events.jsonl``) — the
    fine-grained state machine (TASK_CREATED/RUNNING/SUCCEEDED/FAILED/...);
  * the **result bundles** (``results/*.validation.json``) — the verdicts.

The timeline is ordered by timestamp (falling back to event ``seq``), so an
operator can replay the run step by step and cross-check the three sources
against each other — if the audit log and the event store disagree, that
discrepancy is itself visible.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import audit


def _resolve_run_dir(run_id_or_dir: str | Path) -> Path | None:
    """Accept either a run_id (looked up in the audit log) or a run directory."""
    p = Path(run_id_or_dir)
    if (p / "events").is_dir() or (p / "RUN_CONFIG.json").exists():
        return p
    for r in audit.read():
        if r.get("event") == "run_started" and r.get("run_id") == str(run_id_or_dir):
            rd = Path(r.get("run_dir", ""))
            if rd.exists():
                return rd
    return None


def _read_events(run_dir: Path) -> list[dict[str, Any]]:
    f = run_dir / "events" / "run.events.jsonl"
    out: list[dict[str, Any]] = []
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def trace(run_id_or_dir: str | Path) -> dict[str, Any]:
    """Reconstruct the full timeline for a run from audit + events + bundles."""
    run_dir = _resolve_run_dir(run_id_or_dir)
    if run_dir is None:
        return {"found": False, "run_id": str(run_id_or_dir), "timeline": []}

    run_id = run_dir.name
    cfg_path = run_dir / "RUN_CONFIG.json"
    if cfg_path.exists():
        try:
            run_id = json.loads(cfg_path.read_text(encoding="utf-8")).get("run_id", run_id)
        except (OSError, json.JSONDecodeError):
            pass

    timeline: list[dict[str, Any]] = []

    # 1) audit records scoped to this run (machine-wide spine).
    for r in audit.read():
        rid = r.get("run_id") or r.get("harness_run_id")
        rd = r.get("run_dir")
        if rid == run_id or (rd and Path(rd) == run_dir):
            timeline.append({"source": "audit", "ts": r.get("ts"), "seq": None,
                             "kind": r.get("event"), "detail": r})

    # 2) per-run state-machine events.
    for e in _read_events(run_dir):
        timeline.append({"source": "events", "ts": e.get("ts"), "seq": e.get("seq"),
                         "kind": e.get("event"), "detail": e})

    # 3) terminal verdicts from the result bundles.
    bundles: list[dict[str, Any]] = []
    results = run_dir / "results"
    if results.is_dir():
        for f in sorted(results.glob("*.validation.json")):
            try:
                b = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            bundles.append(b)
            timeline.append({"source": "bundle", "ts": None, "seq": None,
                             "kind": "VERDICT", "detail": {
                                 "task_id": b.get("task_id"),
                                 "status_technical": b.get("status_technical"),
                                 "status_scientific": b.get("status_scientific"),
                                 "degenerate": b.get("degenerate"),
                                 "blocked": b.get("blocked", False)}})

    # Order by timestamp, then by event seq; record-less keys sort last but stable.
    def _key(item: dict[str, Any]) -> tuple[str, int]:
        seq = item.get("seq")
        return (str(item.get("ts") or "~"), int(seq) if seq is not None else 1 << 30)

    timeline.sort(key=_key)
    return {"found": True, "run_id": run_id, "run_dir": str(run_dir),
            "n_events": len(timeline), "n_tasks": len(bundles), "timeline": timeline}


def format_trace(tr: dict[str, Any]) -> str:
    """Render a trace as an operator-readable timeline."""
    if not tr.get("found"):
        return f"run {tr.get('run_id')!r} not found in audit log or on disk"
    lines = [f"TRACE run={tr['run_id']}  dir={tr['run_dir']}",
             f"  {tr['n_events']} timeline entries across audit+events+bundles, "
             f"{tr['n_tasks']} task verdict(s)", ""]
    tag = {"audit": "AUDIT ", "events": "EVENT ", "bundle": "VERDICT"}
    for it in tr["timeline"]:
        ts = it.get("ts") or "--"
        src = tag.get(it["source"], it["source"])
        d = it["detail"]
        extra = ""
        if it["source"] == "bundle":
            extra = (f"{d.get('task_id')} -> tech={d.get('status_technical')} "
                     f"sci={d.get('status_scientific')}"
                     + (" BLOCKED" if d.get("blocked") else "")
                     + (" DEGENERATE" if d.get("degenerate") else ""))
        elif it["source"] == "events":
            extra = " ".join(f"{k}={d[k]}" for k in ("task_id", "tool_id", "reason", "state")
                             if k in d and d[k] is not None)
        else:  # audit
            extra = " ".join(f"{k}={d[k]}" for k in ("tool", "task_id", "reason", "anomalies")
                             if k in d and d[k] is not None)
        lines.append(f"  {ts}  {src}  {it['kind']:20s} {extra}")
    return "\n".join(lines)
