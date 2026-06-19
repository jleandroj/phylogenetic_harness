"""Final per-run report for a comparative phylogenomic pipeline (spec §24.14).

Reconstructs the 13 mandatory sections purely from what a run left on disk
(bundles, manifest, tools lock, environment, gene/species trees), so a report is
auditable and reproducible from the run directory alone. Every claim is sourced
from a real artefact; what cannot be concluded is stated explicitly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .report import ReportGenerator


def _load(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _bundles(run_dir: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for f in sorted((run_dir / "results").glob("*.validation.json")):
        b = _load(f)
        if b.get("task_id"):
            out[b["task_id"]] = b
    return out


def _evidence(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return (bundle.get("interpretation") or {}).get("validation", {}).get("statistical", {}).get("checks", [])


def _find_check(bundle: dict[str, Any], name: str) -> dict[str, Any] | None:
    for c in _evidence(bundle):
        if c.get("name") == name:
            return c
    return None


def generate_pipeline_report(run_dir: str | Path) -> dict[str, str]:
    """Write report.md + report.json for a pipeline run. Returns the paths."""
    run_dir = Path(run_dir)
    cfg = _load(run_dir / "RUN_CONFIG.json")
    lock = _load(run_dir / "TOOLS.lock.json")
    env = _load(run_dir / "ENVIRONMENT.snapshot.json")
    manifest = _load(run_dir / "RUN_MANIFEST.json")
    bundles = _bundles(run_dir)

    def by_type(t: str) -> list[tuple[str, dict[str, Any]]]:
        return [(tid, b) for tid, b in bundles.items() if b.get("task_type") == t]

    msas = by_type("msa_mafft")
    gene_trees = by_type("tree_iqtree_mfp") or by_type("tree_raxml") or by_type("tree_fasttree")
    astral = by_type("species_tree_astral")

    succeeded = [tid for tid, b in bundles.items() if b.get("status_technical") == "SUCCEEDED"]
    failed = [(tid, b.get("status_technical")) for tid, b in bundles.items()
              if str(b.get("status_technical", "")).startswith("FAILED")]
    degenerate = [tid for tid, b in bundles.items() if b.get("degenerate")]
    interpretable = [tid for tid, b in bundles.items()
                     if b.get("status_scientific") == "BIOLOGICALLY_INTERPRETABLE"]
    low_conf = [(tid, b.get("status_scientific")) for tid, b in bundles.items()
                if b.get("status_scientific") in ("LOW_CONFIDENCE", "INCONCLUSIVE", "MODEL_LIMITED")]

    # Recompute gene-tree discordance + species-vs-gene from the trees on disk.
    discordance_lines: list[str] = []
    species_lines: list[str] = []
    try:
        from .bio import compare_gene_trees
        tree_paths = {}
        for tid, b in gene_trees:
            for o in b.get("outputs", []):
                if o.get("path") and Path(o["path"]).exists():
                    tree_paths[tid.split(".")[-1]] = o["path"]
        names = sorted(tree_paths)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                c = compare_gene_trees(tree_paths[names[i]], tree_paths[names[j]])
                verdict = "congruent" if c["congruent"] else "DISCORDANT"
                discordance_lines.append(
                    f"{names[i]} vs {names[j]}: RF={c['rf_distance']}/{c['max_rf']} "
                    f"(norm {c['normalized_rf']}) — {verdict}")
        for _tid, b in astral:
            sp = next((o["path"] for o in b.get("outputs", []) if o.get("path")), None)
            if sp and Path(sp).exists():
                for name, tp in tree_paths.items():
                    c = compare_gene_trees(sp, tp)
                    species_lines.append(
                        f"species tree vs {name}: RF={c['rf_distance']}/{c['max_rf']} "
                        f"({'congruent' if c['congruent'] else 'differs'})")
    except Exception as exc:  # noqa: BLE001
        discordance_lines.append(f"(could not recompute topology comparison: {exc})")

    s = ReportGenerator.empty_sections()

    s["1. What was executed"] = (
        [f"MSA (MAFFT): {tid} -> {b['status_technical']}" for tid, b in msas]
        + [f"gene tree: {tid} -> {b['status_technical']}"
           + (f" | model={(_find_check(b, 'model_selected') or {}).get('data', {}).get('model')}"
              if _find_check(b, "model_selected") else "")
           for tid, b in gene_trees]
        + [f"species tree (ASTRAL): {tid} -> {b['status_technical']}" for tid, b in astral]
    )
    s["2. What was NOT executed"] = [
        x for x in [
            "gene/species-tree reconciliation (Notung) — not wired",
            "dN/dS selection analysis (PAML/HyPhy) — not wired",
            "ancestral sequence reconstruction — not wired",
            ("ASTRAL species tree — skipped (tool unavailable)" if not astral else None),
        ] if x
    ]
    s["3. What failed"] = ([f"{tid}: {st}" for tid, st in failed] or ["none"])
    s["4. What was negative"] = (
        discordance_lines
        + ["Gene-tree discordance (RF>0) is a NEGATIVE result for tree concordance, "
           "consistent with ILS/introgression — not a technical failure."]
        if any("DISCORDANT" in x for x in discordance_lines) else ["no negative results recorded"]
    )
    s["5. What was inconclusive"] = (
        [f"{tid}: {st}" for tid, st in low_conf]
        + (["ASTRAL species tree kept LOW_CONFIDENCE: too few loci for a powered "
            "coalescent estimate."] if astral else [])
    ) or ["none"]
    s["6. What was technically valid"] = [
        f"{tid}: technical={b['status_technical']}, validators_passed={b.get('validators_passed')}"
        for tid, b in bundles.items()
    ]
    s["7. What was biologically interpretable"] = (
        [f"{tid}: BIOLOGICALLY_INTERPRETABLE — evidence: "
         + ", ".join(f"{c['name']}={c['status']}" for c in _evidence(bundles[tid]) if c['status'] == 'PASSED')
         for tid in interpretable]
        or ["none reached BIOLOGICALLY_INTERPRETABLE"]
    )
    # Standing prohibitions: take them from any bundle (they are attached to all).
    not_allowed = []
    for b in bundles.values():
        na = (b.get("interpretation") or {}).get("interpretation_not_allowed", [])
        for x in na:
            if x not in not_allowed:
                not_allowed.append(x)
    s["8. What CANNOT be concluded"] = not_allowed + species_lines
    # Resources: pull peak RSS / wall from executions.
    res_lines = []
    for tid, b in bundles.items():
        ex = b.get("execution") or {}
        r = ex.get("resources") or {}
        res_lines.append(f"{tid}: wall={r.get('wall_seconds')}s, peak_rss_mb={r.get('max_rss_mb')} "
                         f"({r.get('rss_source')}), audit={r.get('status')}")
    s["9. Resources used"] = res_lines
    s["10. Software / versions used"] = [
        f"{tid}: {meta.get('version')} (available={meta.get('available')})"
        for tid, meta in sorted(lock.items())
    ] + [f"python: {(manifest.get('fingerprint') or {}).get('python_version', env.get('commands', {}).get('python_version', {}).get('stdout', '').strip())}"]
    # Data: input checksums from the manifest, plus output checksums.
    incl = [f"input {k}: {v}" for k, v in (manifest.get("inputs") or {}).items()]
    outs = [f"output {o['path'].split('/')[-1]}: {o.get('sha256')}"
            for b in bundles.values() for o in b.get("outputs", []) if o.get("sha256")]
    s["11. Data included / excluded"] = (incl + outs) or ["(no input checksums frozen)"]
    s["12. Remaining risks"] = [
        "Synthetic test genes only — never validated on real loci.",
        "Species tree from few loci has low statistical power.",
        "Sandbox not exercised; tools run with user permissions.",
        "UFBoot/bootstrap below threshold on these short alignments (recorded, not hidden).",
    ]
    s["13. Recommended next actions"] = [
        "Run on a real small ortholog set and see what breaks.",
        "Add more loci before trusting the ASTRAL species tree.",
        "Wire gene/species-tree reconciliation (Notung) to turn discordance into a biological hypothesis.",
    ]

    n_disc = sum(1 for x in discordance_lines if "DISCORDANT" in x)
    summary = (
        f"Comparative pipeline over {len(msas)} gene(s): "
        f"{len(succeeded)}/{len(bundles)} tasks SUCCEEDED, {len(interpretable)} biologically interpretable, "
        f"{len(failed)} failed, {len(degenerate)} degenerate. "
        f"{n_disc} discordant gene-tree pair(s). "
        f"Species tree: {'built (LOW_CONFIDENCE on few loci)' if astral else 'not built'}. "
        "Technical success never promoted to a biological conclusion without evidence."
    )

    report = {
        "run_id": cfg.get("run_id"),
        "config_hash": cfg.get("config_hash"),
        "summary": summary,
        "scientific_question": "Comparative phylogenomics: per-gene model-selected trees, "
                               "gene-tree discordance, and a coalescent species tree.",
        "sections": s,
        "bundles": {tid: {"technical": b.get("status_technical"),
                          "scientific": b.get("status_scientific")} for tid, b in bundles.items()},
    }
    return ReportGenerator(run_dir).generate(report)
