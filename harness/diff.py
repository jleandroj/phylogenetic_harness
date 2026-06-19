"""Rigorous run-to-run comparison (audit Q5: "compare today's run vs last week").

``diff_runs`` names the drift instead of guessing: it compares the frozen config
hash, the tool lockfile versions, the seed, and — per task — the technical state,
scientific state and output checksum. The result is a structured report with
explicit ``config_drift / version_drift / seed_drift / result_drift`` and an
``unchanged`` boolean.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _bundles(run_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for f in sorted((run_dir / "results").glob("*.validation.json")):
        data = _load(f)
        if data.get("task_id"):
            out[data["task_id"]] = data
    return out


def _output_shas(bundle: dict[str, Any]) -> list[str | None]:
    return [o.get("sha256") for o in bundle.get("outputs", [])]


def diff_runs(run_a: str | Path, run_b: str | Path) -> dict[str, Any]:
    a, b = Path(run_a), Path(run_b)
    cfg_a, cfg_b = _load(a / "RUN_CONFIG.json"), _load(b / "RUN_CONFIG.json")
    lock_a, lock_b = _load(a / "TOOLS.lock.json"), _load(b / "TOOLS.lock.json")

    config_drift = []
    if cfg_a.get("config_hash") != cfg_b.get("config_hash"):
        config_drift.append(f"config_hash {cfg_a.get('config_hash')} -> {cfg_b.get('config_hash')}")

    seed_drift = []
    if cfg_a.get("seed") != cfg_b.get("seed"):
        seed_drift.append(f"seed {cfg_a.get('seed')} -> {cfg_b.get('seed')}")

    version_drift = []
    for tool in sorted(set(lock_a) | set(lock_b)):
        va = (lock_a.get(tool) or {}).get("version")
        vb = (lock_b.get(tool) or {}).get("version")
        if va != vb:
            version_drift.append(f"{tool} {va} -> {vb}")

    ba, bb = _bundles(a), _bundles(b)
    result_drift = []
    for task_id in sorted(set(ba) | set(bb)):
        if task_id not in ba or task_id not in bb:
            result_drift.append(f"{task_id}: present in only one run")
            continue
        ta, tb = ba[task_id], bb[task_id]
        if ta.get("status_technical") != tb.get("status_technical"):
            result_drift.append(
                f"{task_id}: technical {ta.get('status_technical')} -> {tb.get('status_technical')}")
        if ta.get("status_scientific") != tb.get("status_scientific"):
            result_drift.append(
                f"{task_id}: scientific {ta.get('status_scientific')} -> {tb.get('status_scientific')}")
        if _output_shas(ta) != _output_shas(tb):
            result_drift.append(f"{task_id}: output checksum differs")

    unchanged = not (config_drift or seed_drift or version_drift or result_drift)
    return {
        "run_a": str(a),
        "run_b": str(b),
        "config_drift": config_drift,
        "seed_drift": seed_drift,
        "version_drift": version_drift,
        "result_drift": result_drift,
        "unchanged": unchanged,
    }
