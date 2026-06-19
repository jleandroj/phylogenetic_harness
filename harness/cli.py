"""Command-line interface (``python -m harness``).

Subcommands:
    capture-env       Capture the environment into a directory (spec §7).
    validate-manifest Load and checksum a dataset manifest (spec §24.1).
    demo-run          End-to-end auditable run producing a final report (spec §24.14).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import clock
from .datasets import DatasetManifest
from .environment import capture_environment
from .report import ReportGenerator
from .run import Run, RunConfig, new_run_id
from .science import build_interpretation
from .tasks import FailurePolicy, ResourceRequest, Task


def _cmd_capture_env(args: argparse.Namespace) -> int:
    out = Path(args.out)
    snap = capture_environment(out, timestamp_iso=clock.iso_now(), disk_path=out if out.exists() else ".")
    sys.stderr.write(f"environment captured -> {out}\n")
    sys.stdout.write(json.dumps({"host": snap["hardware"]["hostname"],
                                 "tools_present": sorted(k for k, v in snap["tools"].items() if v["present"])},
                                indent=2) + "\n")
    return 0


def _cmd_validate_manifest(args: argparse.Namespace) -> int:
    manifest = DatasetManifest.load(args.manifest).compute_checksums()
    low = manifest.low_quality_inputs()
    sys.stdout.write(json.dumps({
        "dataset_id": manifest.dataset_id,
        "n_inputs": len(manifest.inputs),
        "low_quality_inputs": [i.sample_id for i in low],
        "taxa_include": manifest.taxa_include,
    }, indent=2) + "\n")
    return 0


def _cmd_demo_run(args: argparse.Namespace) -> int:
    cfg = RunConfig(run_id=args.run_id or new_run_id(), mode="test", executor="local")
    run = Run(cfg)
    run.capture_environment()
    tools_dir = Path(__file__).resolve().parent.parent / "tools"
    if tools_dir.exists():
        run.load_tools(tools_dir)

    # A trivial, registered, harmless task: print samtools version (or echo).
    tool_id = "samtools" if "samtools" in run.tools.all() and run.tools.get("samtools").available else None
    if tool_id:
        command = "samtools --version"
    else:
        tool_id = "coreutils-echo"
        command = "echo harness-demo"

    out_file = run.dir / "results" / "demo.txt"
    task = Task(
        task_id=f"{cfg.run_id}.task_000001",
        run_id=cfg.run_id,
        task_type="demo",
        tool_id=tool_id,
        command_template=command + " > {out}",
        inputs=["<none: version probe>"],
        outputs_expected=[str(out_file)],
        validators=["file_exists", "file_nonempty"],
        resources=ResourceRequest(cpus=1, memory_gb=1),
        failure_policy=FailurePolicy(timeout_seconds=60),
    )
    run.approval.check(task)  # harmless task: no approval required

    result = run.executor.run(task.task_id, task.render_command(out=out_file), timeout_seconds=60)
    checks = run.validators.run_many(task.validators, out_file)

    interp = build_interpretation(
        checks,
        allowed=["The command executed and produced a non-empty output file."],
        limitations=["This demo proves auditability only, not any biological claim."],
    )

    sections = ReportGenerator.empty_sections()
    sections["1. What was executed"] = [f"task {task.task_id}: `{result.command}` (exit={result.exit_code})"]
    sections["6. What was technically valid"] = [f"{c.name}: {c.status}" for c in checks]
    sections["7. What was biologically interpretable"] = [
        f"scientific_state={interp.scientific_state.value}; confidence={interp.confidence}"
    ]
    sections["8. What CANNOT be concluded"] = interp.interpretation_not_allowed
    sections["9. Resources used"] = [json.dumps(result.resources.to_dict()) if result.resources else "n/a"]
    sections["10. Software / versions used"] = [
        f"{tid}: {c.detected_version} (available={c.available})" for tid, c in run.tools.all().items()
    ]
    sections["12. Remaining risks"] = ["v1 core only; heavy bio tools not yet executed"]
    sections["13. Recommended next actions"] = ["Wire a real alignment/tree task with full validators"]

    paths = run.report.generate({
        "run_id": cfg.run_id,
        "summary": "Demo run exercising the auditable core end-to-end.",
        "scientific_question": "none (infrastructure smoke test)",
        "sections": sections,
        "interpretation": interp.to_dict(),
        "execution": result.to_dict(),
    })
    run.finish()
    sys.stdout.write(json.dumps({"run_dir": str(run.dir), "report": paths,
                                 "scientific_state": interp.scientific_state.value}, indent=2) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="harness", description="AI-assisted phylogenomics harness")
    sub = p.add_subparsers(dest="command", required=True)

    pe = sub.add_parser("capture-env", help="capture the environment")
    pe.add_argument("--out", default="runs/env_snapshot")
    pe.set_defaults(func=_cmd_capture_env)

    pm = sub.add_parser("validate-manifest", help="validate a dataset manifest")
    pm.add_argument("manifest")
    pm.set_defaults(func=_cmd_validate_manifest)

    pd = sub.add_parser("demo-run", help="end-to-end demo run")
    pd.add_argument("--run-id", dest="run_id", default=None)
    pd.set_defaults(func=_cmd_demo_run)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
