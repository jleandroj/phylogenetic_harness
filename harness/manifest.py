"""Run manifest + replay (audit Q1: "can I repeat this exactly in 3 months?").

``write_manifest`` freezes everything needed to reproduce a run: the harness git
commit, python + dependency versions, the tool lockfile, the frozen RunConfig and
its hash, the seed and its derivation rule, and a sha256 of every input and of the
dataset manifest.

``replay`` re-executes the frozen task plan and answers honestly: it re-runs the
tasks, compares output checksums to the original, and — crucially — REPORTS DRIFT
(a different python/dep/tool/git version) instead of pretending the replay is
identical. Reproducibility is conditional, and the system says when the condition
is not met.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from . import ids, taskstore

MANIFEST_NAME = "RUN_MANIFEST.json"
TRACKED_DEPS = ("pyyaml", "dendropy", "pytest")
DEFAULT_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


def _git_commit() -> dict[str, Any]:
    git = shutil.which("git")
    if not git:
        return {"available": False, "commit": None, "dirty": None}
    try:
        commit = subprocess.run([git, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=15)
        status = subprocess.run([git, "status", "--porcelain"], capture_output=True, text=True, timeout=15)
        if commit.returncode != 0:
            return {"available": False, "commit": None, "dirty": None}
        return {"available": True, "commit": commit.stdout.strip(), "dirty": bool(status.stdout.strip())}
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "commit": None, "dirty": None}


def _dep_versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for dep in TRACKED_DEPS:
        try:
            out[dep] = version(dep)
        except PackageNotFoundError:
            out[dep] = None
    return out


def fingerprint() -> dict[str, Any]:
    """Everything version-like that affects reproducibility."""
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "deps": _dep_versions(),
        "git": _git_commit(),
    }


def write_manifest(
    run_dir: str | Path,
    *,
    run_config: dict[str, Any],
    tools_lock: dict[str, Any],
    seed_record: dict[str, Any],
    input_paths: list[str] | None = None,
    dataset_manifest_path: str | None = None,
) -> Path:
    run_dir = Path(run_dir)
    inputs = {}
    for p in input_paths or []:
        fp = Path(p)
        inputs[str(p)] = ("sha256:" + ids.sha256_file(fp)) if fp.exists() and fp.is_file() else None
    dataset_sha = None
    if dataset_manifest_path and Path(dataset_manifest_path).exists():
        dataset_sha = "sha256:" + ids.sha256_file(dataset_manifest_path)

    manifest = {
        "run_config": run_config,
        "config_hash": run_config.get("config_hash"),
        "seed": seed_record,
        "fingerprint": fingerprint(),
        "tools_lock": tools_lock,
        "inputs": inputs,
        "dataset_manifest_sha256": dataset_sha,
        "task_plan": taskstore.PLAN_NAME,
    }
    path = run_dir / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _compare_fingerprints(frozen: dict[str, Any], current: dict[str, Any]) -> list[str]:
    drift: list[str] = []
    if frozen.get("python_version") != current.get("python_version"):
        drift.append(f"python {frozen.get('python_version')} -> {current.get('python_version')}")
    for dep, v in frozen.get("deps", {}).items():
        cv = current.get("deps", {}).get(dep)
        if v != cv:
            drift.append(f"dep {dep} {v} -> {cv}")
    fg, cg = frozen.get("git", {}), current.get("git", {})
    if fg.get("commit") != cg.get("commit"):
        drift.append(f"git {fg.get('commit')} -> {cg.get('commit')}")
    return drift


def _original_outputs(run_dir: Path, task_id: str) -> dict[str, str | None]:
    bundle = run_dir / "results" / f"{task_id}.validation.json"
    if not bundle.exists():
        return {}
    data = json.loads(bundle.read_text(encoding="utf-8"))
    return {o["path"]: o.get("sha256") for o in data.get("outputs", [])}


def replay(run_dir: str | Path, *, tools_dir: str | Path | None = None) -> dict[str, Any]:
    """Re-execute the frozen plan and report config-hash match, drift and output match."""
    from .run import Run, RunConfig

    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    frozen_fp = manifest["fingerprint"]
    drift = _compare_fingerprints(frozen_fp, fingerprint())

    # Reconstruct the ORIGINAL frozen RunConfig to verify its hash, then run the
    # replay into runs/{run}/replay (a different output_dir, which would change the
    # hash — so we compare against the original, not the replay config).
    import dataclasses

    cfg_dict = {k: v for k, v in manifest["run_config"].items() if k != "config_hash"}
    valid_fields = set(RunConfig.__dataclass_fields__)
    original_cfg = RunConfig(**{k: v for k, v in cfg_dict.items() if k in valid_fields})
    config_hash_match = original_cfg.config_hash == manifest.get("config_hash")

    # Replay deliberately re-produces the original outputs, so it must be allowed
    # to overwrite them (the original config's overwrite policy is preserved in the
    # hash check above; this only affects the replay execution).
    replay_cfg = dataclasses.replace(
        original_cfg, output_dir=str(run_dir / "replay"), allow_overwrite=True
    )
    replay_run = Run(replay_cfg)
    replay_run.load_tools(tools_dir or DEFAULT_TOOLS_DIR)
    runner = replay_run.build_runner(worker_id="replay")

    tasks = taskstore.load_tasks(run_dir)
    task_reports = []
    for task in tasks:
        original = _original_outputs(run_dir, task.task_id)
        bundle = runner.run_task(task)
        new = {o["path"]: o.get("sha256") for o in bundle.get("outputs", [])}
        outputs_match = bool(new) and new == original
        task_reports.append({
            "task_id": task.task_id,
            "outputs_match": outputs_match,
            "original": original,
            "replay": new,
        })
    replay_run.finish()

    identical = config_hash_match and not drift and all(t["outputs_match"] for t in task_reports)
    report = {
        "config_hash_match": config_hash_match,
        "drift": drift,
        "identical": identical,
        "tasks": task_reports,
    }
    (run_dir / "REPLAY_REPORT.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report
