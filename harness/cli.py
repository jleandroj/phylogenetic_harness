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
    # Persist the plan + freeze a manifest so the run is resumable and replayable.
    from . import manifest, taskstore
    from .aggregate import aggregate_run
    taskstore.save_tasks(run.dir, [task])
    runner = run.build_runner()
    bundle = runner.run_task(
        task,
        allowed=["The registered tool executed and produced a non-empty output file."],
        limitations=["This demo proves auditability only, not any biological claim."],
    )
    tools_lock = json.loads((run.dir / "TOOLS.lock.json").read_text())
    manifest.write_manifest(
        run.dir,
        run_config={**cfg.to_dict(), "config_hash": cfg.config_hash},
        tools_lock=tools_lock,
        seed_record=run.seeds.record(),
        input_paths=[i for i in task.inputs if Path(i).exists()],
    )
    aggregate_run(run.dir)

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


def _cmd_aggregate(args: argparse.Namespace) -> int:
    from .aggregate import aggregate_run
    out = aggregate_run(args.run_dir)
    sys.stdout.write(json.dumps({"results_csv": str(out)}, indent=2) + "\n")
    return 0


def _cmd_phylo(args: argparse.Namespace) -> int:
    """Real slice: MAFFT alignment -> FastTree tree on an input FASTA."""
    from . import manifest
    from .aggregate import aggregate_run
    from .bio import run_phylo_slice
    cfg = RunConfig(run_id=args.run_id or new_run_id(), mode="full", executor="local")
    run = Run(cfg)
    run.capture_environment()
    run.load_tools(manifest.DEFAULT_TOOLS_DIR)
    run.write_tools_lock()
    out = run_phylo_slice(run.build_runner(), run_id=cfg.run_id,
                          fasta_path=args.fasta, workdir=run.dir / "work")
    # Persist plan + manifest + CSV for replay/diff/aggregate.
    aggregate_run(run.dir)
    run.finish()
    tree = out.get("tree") or {}
    sys.stdout.write(json.dumps({
        "run_dir": str(run.dir),
        "msa": out["msa"]["status_technical"],
        "tree": tree.get("status_technical"),
        "scientific_state": tree.get("status_scientific"),
        "confidence": (tree.get("interpretation") or {}).get("confidence"),
    }, indent=2) + "\n")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    """Comparative slice: align + RAxML-bootstrap a tree per gene, compare topologies."""
    from . import manifest
    from .aggregate import aggregate_run
    from .bio import run_comparative_slice
    genes = {}
    for spec in args.genes:
        if "=" not in spec:
            sys.stderr.write(f"gene spec must be name=path, got {spec!r}\n")
            return 2
        name, path = spec.split("=", 1)
        genes[name] = path
    cfg = RunConfig(run_id=args.run_id or new_run_id(), mode="full", executor="local")
    run = Run(cfg)
    run.capture_environment()
    run.load_tools(manifest.DEFAULT_TOOLS_DIR)
    run.write_tools_lock()
    out = run_comparative_slice(run.build_runner(), run_id=cfg.run_id, genes=genes,
                                workdir=run.dir / "work", nboot=args.nboot)
    aggregate_run(run.dir)
    run.finish()
    sys.stdout.write(json.dumps({
        "run_dir": str(run.dir),
        "genes": {n: {"tree": (g.get("tree") or {}).get("status_technical"),
                      "scientific": (g.get("tree") or {}).get("status_scientific")}
                  for n, g in out["genes"].items()},
        "comparisons": out["comparisons"],
        "discordant": out["discordant"],
        "note": out["note"],
    }, indent=2) + "\n")
    return 0


def _cmd_pipeline(args: argparse.Namespace) -> int:
    """Full comparative pipeline: per-gene model selection (IQ-TREE) + ASTRAL
    species tree + gene-tree discordance."""
    from . import manifest
    from .aggregate import aggregate_run
    from .bio import run_phylogenomic_pipeline
    genes = {}
    for spec in args.genes:
        if "=" not in spec:
            sys.stderr.write(f"gene spec must be name=path, got {spec!r}\n")
            return 2
        name, path = spec.split("=", 1)
        genes[name] = path
    # Sandbox tool execution by default when a backend exists (audit round 4 #3).
    import shutil
    backend_present = bool(shutil.which("bwrap") or shutil.which("apptainer"))
    sandbox = backend_present and not args.no_sandbox
    # Production posture by default: strict policy enforcement (guarantee #3). A
    # non-compliant config is BLOCKED up front rather than running unprotected.
    strict = not getattr(args, "no_strict", False) and sandbox
    cfg = RunConfig(run_id=args.run_id or new_run_id(), mode="full", executor="local",
                    sandbox=sandbox, strict=strict)
    run = Run(cfg)
    if not backend_present and not args.no_sandbox:
        sys.stderr.write("note: no sandbox backend (bwrap/apptainer) found; running unsandboxed\n")
    run.capture_environment()
    run.load_tools(manifest.DEFAULT_TOOLS_DIR)
    run.write_tools_lock()
    loci_independent = {"yes": True, "no": False, "unknown": None}[args.loci_independent]
    out = run_phylogenomic_pipeline(
        run.build_runner(), run_id=cfg.run_id, genes=genes, workdir=run.dir / "work",
        nboot=args.nboot, model_selection=not args.no_model_selection,
        species_tree=not args.no_species_tree, loci_independent=loci_independent,
    )
    # Freeze provenance (Q1) so the run is reproducible/auditable.
    tools_lock = json.loads((run.dir / "TOOLS.lock.json").read_text())
    manifest.write_manifest(
        run.dir, run_config={**cfg.to_dict(), "config_hash": cfg.config_hash},
        tools_lock=tools_lock, seed_record=run.seeds.record(),
        input_paths=[p for p in genes.values() if Path(p).exists()],
    )
    aggregate_run(run.dir)
    from .bio_report import generate_pipeline_report
    report_paths = generate_pipeline_report(run.dir)
    run.finish()
    sp = out["species_tree"] or {}
    sys.stdout.write(json.dumps({
        "run_dir": str(run.dir),
        "report": report_paths,
        "model_selection": out["model_selection"],
        "genes": {n: {"method": g.get("method"), "model": g.get("model"),
                      "tree": (g.get("tree") or {}).get("status_technical"),
                      "scientific": (g.get("tree") or {}).get("status_scientific")}
                  for n, g in out["genes"].items()},
        "loci_independent": out.get("loci_independent"),
        "discordant_supported": out["discordant"],          # well-supported conflict only
        "discordant_raw_rf": out.get("raw_discordant_rf"),  # raw RF (includes noise)
        "min_support_threshold": out.get("min_support_threshold"),
        "comparisons": out["comparisons"],
        "species_tree": ({"status": sp.get("species", {}).get("status_technical") if sp.get("species") else None,
                          "scientific": (sp.get("species") or {}).get("status_scientific") if sp.get("species") else None,
                          "path": sp.get("species_path"), "skipped": sp.get("skipped"),
                          "vs_genes": sp.get("vs_genes")}),
    }, indent=2) + "\n")
    return 0


def _cmd_kill(args: argparse.Namespace) -> int:
    """Kill-switch: stop a run (or --panic to stop ALL runs)."""
    from . import audit, killswitch
    if args.panic:
        p = killswitch.panic()
        audit.record("panic", path=str(p))
        sys.stderr.write(f"PANIC: global STOP set at {p} — all runs will abort at their next task\n")
    elif args.run_dir:
        p = killswitch.stop(args.run_dir)
        audit.record("kill", run_dir=args.run_dir, path=str(p))
        sys.stderr.write(f"STOP set at {p} — that run aborts at its next task\n")
    else:
        sys.stderr.write("usage: harness kill <run_dir> | harness kill --panic\n")
        return 2
    return 0


def _cmd_trace(args: argparse.Namespace) -> int:
    """Reconstruct exactly what happened in a run (audit + events + verdicts)."""
    from . import trace as _trace
    tr = _trace.trace(args.run)
    if args.json:
        sys.stdout.write(json.dumps(tr, indent=2, default=str) + "\n")
    else:
        sys.stdout.write(_trace.format_trace(tr) + "\n")
    return 0 if tr.get("found") else 1


def _cmd_runs(args: argparse.Namespace) -> int:
    """Catalogue of every run + its outcome (technical + scientific verdicts)."""
    from .registry import list_runs
    runs = list_runs()
    sys.stdout.write(json.dumps(runs, indent=2) + "\n")
    sys.stderr.write(f"{len(runs)} run(s); "
                     f"{sum(1 for r in runs if not r['finished'])} unfinished\n")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    """Operator-facing view of EVERYTHING: runs + tool calls (incl. out-of-harness)."""
    from . import audit
    if args.verify:
        v = audit.verify()
        sys.stdout.write(json.dumps(v, indent=2) + "\n")
        return 0 if v["ok"] else 1
    if args.full:
        for r in audit.read():
            sys.stdout.write(json.dumps(r, sort_keys=True) + "\n")
        return 0
    sys.stdout.write(json.dumps(audit.summary(), indent=2) + "\n")
    return 0


def _cmd_genome_phylo(args: argparse.Namespace) -> int:
    """Whole-genome alignment-free phylogeny (Mash + NJ), fully inside the harness."""
    import shutil

    from . import manifest
    from .aggregate import aggregate_run
    from .genome_phylo import run_genome_phylogeny
    genomes = {}
    for spec in args.genomes:
        if "=" not in spec:
            sys.stderr.write(f"genome spec must be label=path, got {spec!r}\n")
            return 2
        label, path = spec.split("=", 1)
        genomes[label] = path
    backend_present = bool(shutil.which("bwrap") or shutil.which("apptainer"))
    sandbox = backend_present and not args.no_sandbox
    strict = not getattr(args, "no_strict", False) and sandbox
    cfg = RunConfig(run_id=args.run_id or new_run_id(), mode="full", executor="local",
                    sandbox=sandbox, strict=strict)
    run = Run(cfg)
    run.capture_environment()
    run.load_tools(manifest.DEFAULT_TOOLS_DIR)
    run.write_tools_lock()
    out = run_genome_phylogeny(
        run.build_runner(), run_id=cfg.run_id, genomes=genomes, workdir=run.dir / "work",
        k=args.k, sketch_size=args.sketch_size, outgroup=args.outgroup,
        reconstructed=set(args.reconstructed.split(",")) if args.reconstructed else None)
    tools_lock = json.loads((run.dir / "TOOLS.lock.json").read_text())
    manifest.write_manifest(run.dir, run_config={**cfg.to_dict(), "config_hash": cfg.config_hash},
                            tools_lock=tools_lock, seed_record=run.seeds.record(),
                            input_paths=[p for p in genomes.values() if Path(p).exists()])
    aggregate_run(run.dir)
    run.finish()
    sys.stdout.write(json.dumps({
        "run_dir": str(run.dir),
        "tree": out.get("tree_path"),
        "dist_matrix": out.get("dist_tsv"),
        "reconstructed_taxa": out.get("reconstructed"),
        "scientific_state": out.get("scientific_state"),
    }, indent=2) + "\n")
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    from .resume import resume_run
    summary = resume_run(args.run_dir)
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    from .manifest import replay
    report = replay(args.run_dir)
    sys.stdout.write(json.dumps({
        "config_hash_match": report["config_hash_match"],
        "identical": report["identical"],
        "drift": report["drift"],
        "tasks": [{"task_id": t["task_id"], "outputs_match": t["outputs_match"]} for t in report["tasks"]],
    }, indent=2) + "\n")
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    from .diff import diff_runs
    report = diff_runs(args.run_a, args.run_b)
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="harness", description="AI-assisted phylogenomics harness")
    sub = p.add_subparsers(dest="command", required=True)

    pa = sub.add_parser("aggregate", help="aggregate a run's results into results.csv")
    pa.add_argument("run_dir")
    pa.set_defaults(func=_cmd_aggregate)

    pph = sub.add_parser("phylo", help="real slice: MAFFT align + FastTree tree on a FASTA")
    pph.add_argument("fasta")
    pph.add_argument("--run-id", dest="run_id", default=None)
    pph.set_defaults(func=_cmd_phylo)

    pc = sub.add_parser("compare", help="comparative slice: per-gene RAxML trees + RF discordance")
    pc.add_argument("genes", nargs="+", metavar="name=fasta")
    pc.add_argument("--nboot", type=int, default=100)
    pc.add_argument("--run-id", dest="run_id", default=None)
    pc.set_defaults(func=_cmd_compare)

    pp = sub.add_parser("pipeline", help="full pipeline: model selection (IQ-TREE) + ASTRAL species tree")
    pp.add_argument("genes", nargs="+", metavar="name=fasta")
    pp.add_argument("--nboot", type=int, default=1000)
    pp.add_argument("--no-model-selection", action="store_true")
    pp.add_argument("--no-species-tree", action="store_true")
    pp.add_argument("--no-sandbox", action="store_true", help="disable the default execution sandbox")
    pp.add_argument("--no-strict", action="store_true",
                    help="disable strict production policy enforcement (NOT recommended)")
    pp.add_argument("--loci-independent", choices=["yes", "no", "unknown"], default="unknown",
                    help="assert whether the loci are independent (ASTRAL is invalid on linked loci)")
    pp.add_argument("--run-id", dest="run_id", default=None)
    pp.set_defaults(func=_cmd_pipeline)

    prn = sub.add_parser("runs", help="catalogue every run + its outcome/verdicts")
    prn.set_defaults(func=_cmd_runs)

    pt = sub.add_parser("trace", help="reconstruct exactly what happened in a run")
    pt.add_argument("run", help="run_id or run directory")
    pt.add_argument("--json", action="store_true", help="emit the raw merged timeline as JSON")
    pt.set_defaults(func=_cmd_trace)

    pk = sub.add_parser("kill", help="kill-switch: STOP a run (or --panic to stop ALL runs)")
    pk.add_argument("run_dir", nargs="?", help="run directory to stop")
    pk.add_argument("--panic", action="store_true", help="set the global STOP marker (all runs)")
    pk.set_defaults(func=_cmd_kill)

    pau = sub.add_parser("audit", help="operator view of all runs + tool calls (machine-wide)")
    pau.add_argument("--full", action="store_true", help="print every audit record")
    pau.add_argument("--verify", action="store_true", help="verify the tamper-evident hash chain")
    pau.set_defaults(func=_cmd_audit)

    pg = sub.add_parser("genome-phylo", help="whole-genome alignment-free phylogeny (Mash + NJ)")
    pg.add_argument("genomes", nargs="+", metavar="label=genome.fa")
    pg.add_argument("--outgroup", default=None, help="label substring to root on")
    pg.add_argument("--k", type=int, default=21)
    pg.add_argument("--sketch-size", type=int, default=100000)
    pg.add_argument("--reconstructed", default=None, help="comma-separated labels to force as reconstructed")
    pg.add_argument("--no-sandbox", action="store_true")
    pg.add_argument("--no-strict", action="store_true",
                    help="disable strict production policy enforcement (NOT recommended)")
    pg.add_argument("--run-id", dest="run_id", default=None)
    pg.set_defaults(func=_cmd_genome_phylo)

    prs = sub.add_parser("resume", help="resume a crashed run; finish unfinished tasks")
    prs.add_argument("run_dir")
    prs.set_defaults(func=_cmd_resume)

    pr = sub.add_parser("replay", help="re-execute a frozen run and report drift")
    pr.add_argument("run_dir")
    pr.set_defaults(func=_cmd_replay)

    pdf = sub.add_parser("diff", help="compare two runs (config/version/seed/result drift)")
    pdf.add_argument("run_a")
    pdf.add_argument("run_b")
    pdf.set_defaults(func=_cmd_diff)

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
    # Make HARNESS_TOOL_PATHS dirs visible (explicit, not on import — audit round 4).
    from .toolpaths import ensure_tool_paths
    ensure_tool_paths()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
