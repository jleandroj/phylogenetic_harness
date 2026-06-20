"""Results aggregation into a trustworthy CSV (audit Q4).

The CSV is designed so the system cannot lie *without saying so*. Every row
carries provenance (run, tool version, config hash, seed, output checksum) and
its real states. A degenerate or unvalidated result is marked explicitly via the
``degenerate`` and ``trustworthy`` columns — it can never appear as a clean
result. A task that never reached a terminal validated state is still emitted,
but with ``trustworthy=false`` and its real ``technical_state``.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

COLUMNS = [
    "run_id", "task_id", "task_type", "tool_id", "tool_version",
    "technical_state", "scientific_state", "confidence",
    "degenerate", "validators_passed", "trustworthy",
    "config_hash", "seed", "output_sha256",
]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _row(bundle: dict[str, Any], run_cfg: dict[str, Any], tools_lock: dict[str, Any]) -> dict[str, Any]:
    tech = bundle.get("status_technical")
    sci = bundle.get("status_scientific")
    degenerate = bool(bundle.get("degenerate"))
    validators_passed = bool(bundle.get("validators_passed"))
    # Trustworthy ONLY if it succeeded technically, every validator passed, and it
    # is not degenerate. This is the single boolean a consumer should filter on.
    trustworthy = (tech == "SUCCEEDED") and validators_passed and not degenerate
    tool_id = bundle.get("tool_id") or ""
    outputs = bundle.get("outputs") or []
    out_sha = ";".join(o.get("sha256") or "none" for o in outputs) if outputs else "none"
    return {
        "run_id": run_cfg.get("run_id"),
        "task_id": bundle.get("task_id"),
        "task_type": bundle.get("task_type"),
        "tool_id": tool_id,
        "tool_version": (tools_lock.get(tool_id) or {}).get("version"),
        "technical_state": tech,
        "scientific_state": sci,
        "confidence": (bundle.get("interpretation") or {}).get("confidence"),
        "degenerate": degenerate,
        "validators_passed": validators_passed,
        "trustworthy": trustworthy,
        "config_hash": run_cfg.get("config_hash"),
        "seed": run_cfg.get("seed"),
        "output_sha256": out_sha,
    }


def aggregate_run(run_dir: str | Path) -> Path:
    """Read every results/*.validation.json and write results.csv. Returns its path."""
    run_dir = Path(run_dir)
    run_cfg = _load_json(run_dir / "RUN_CONFIG.json")
    tools_lock = _load_json(run_dir / "TOOLS.lock.json")
    rows = []
    for bundle_file in sorted((run_dir / "results").glob("*.validation.json")):
        rows.append(_row(_load_json(bundle_file), run_cfg, tools_lock))

    out = run_dir / "results.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return out


def read_rows(run_dir: str | Path) -> list[dict[str, str]]:
    with open(Path(run_dir) / "results.csv", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
