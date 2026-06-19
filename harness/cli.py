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
    run.load_tools(tools_dir)
    run.write_tools_lock()

    results = run.dir / "results"
    results.mkdir(parents=True, exist_ok=True)

    # Build a REAL task on a REGISTERED, AVAILABLE tool. Prefer samtools faidx on
    # a tiny FASTA; otherwise cp. No phantom tools, no shell redirection.
    if "samtools" in run.tools.all() and run.tools.get("samtools").available:
        fasta = results / "demo.fa"
        fasta.write_text(">seqA\nACGTACGTACGT\n>seqB\nTTTTACGTAAAA\n", encoding="utf-8")
        tool_id, argv = "samtools", ["samtools", "faidx", str(fasta)]
        out_file = str(fasta) + ".fai"
    else:
        src = results / "src.txt"
        src.write_text("harness-demo\n", encoding="utf-8")
        out_file = str(results / "demo.txt")
        tool_id, argv = "cp", ["cp", str(src), out_file]

    task = Task(
        task_id=f"{cfg.run_id}.task_000001",
        run_id=cfg.run_id,
        task_type="demo",
        tool_id=tool_id,
        command_template=" ".join(argv),
        command_argv=argv,
        inputs=[argv[-1]],
        outputs_expected=[out_file],
        validators=["file_exists", "file_nonempty"],
        resources=ResourceRequest(cpus=1, memory_gb=1),
        failure_policy=FailurePolicy(timeout_seconds=60),
    )

    # THE ONLY execution path: the TaskRunner enforces gate + state + validators.
    runner = run.build_runner()
    bundle = runner.run_task(
        task,
        allowed=["The registered tool executed and produced a non-empty output file."],
        limitations=["This demo proves auditability only, not any biological claim."],
    )

    sections = ReportGenerator.empty_sections()
    sections["1. What was executed"] = [
        f"task {task.task_id}: `{' '.join(argv)}` -> technical={bundle['status_technical']}"
    ]
    sections["6. What was technically valid"] = [
        f"{c['name']}: {c['status']}" for c in bundle["validation"]
    ]
    sections["7. What was biologically interpretable"] = [
        f"scientific_state={bundle['status_scientific']}; "
        f"confidence={bundle['interpretation']['confidence']}"
    ]
    sections["8. What CANNOT be concluded"] = bundle["interpretation"]["interpretation_not_allowed"]
    sections["9. Resources used"] = [json.dumps(bundle["execution"].get("resources"))]
    sections["10. Software / versions used"] = [
        f"{tid}: {c.detected_version} (available={c.available})" for tid, c in run.tools.all().items()
    ]
    sections["12. Remaining risks"] = ["v1 core only; heavy bio tools not yet executed"]
    sections["13. Recommended next actions"] = ["Wire a real alignment/tree task with full validators"]

    paths = run.report.generate({
        "run_id": cfg.run_id,
        "summary": "Demo run exercising the auditable core end-to-end via TaskRunner.",
        "scientific_question": "none (infrastructure smoke test)",
        "sections": sections,
        "bundle": bundle,
    })
    run.finish()
    sys.stdout.write(json.dumps({
        "run_dir": str(run.dir), "report": paths,
        "technical_state": bundle["status_technical"],
        "scientific_state": bundle["status_scientific"],
    }, indent=2) + "\n")
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
