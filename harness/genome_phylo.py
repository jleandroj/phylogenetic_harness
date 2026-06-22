"""Alignment-free whole-genome phylogenomics, INSIDE the harness (auditable).

Runs through the single TaskRunner like everything else: Mash sketch per genome ->
mash paste -> mash dist -> a distance matrix that is validated, then a
Neighbor-Joining tree built from it. The scientific verdict is gated by two real
checks fed to the science layer:

  * the distance matrix must be valid (symmetric, zero diagonal, finite) — a
    technical validator on the dist TSV;
  * OBSERVED-TAXA-ONLY (the lesson from the HomoPan session): a tree that includes
    a RECONSTRUCTED or NON-REPRODUCIBLE genome (e.g. a Cactus ancestor) is a
    DIAGNOSTIC, not a phylogeny of observed taxa. Such a tree is never
    BIOLOGICALLY_INTERPRETABLE, and the harness auto-detects reconstructed inputs
    from a sibling NON_DETERMINISTIC_WARNING.txt or a *.provenance.json with
    determinism.reproducible == false.

This is the proper way to do what was first done by hand outside the harness.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import taskstore
from .runner import TaskRunner
from .task_types import TaskTypeSpec
from .tasks import FailurePolicy, ResourceRequest, Task
from .validators import CheckResult

GENOME_PHYLO_NOT_ALLOWED = [
    "Mash distance is a k-mer (Jaccard) approximation, not a substitution-model distance.",
    "This is a distance (NJ) tree: it has no branch support and no model of evolution.",
]


# ---- task-type specs (each a real audited task) -----------------------------

def mash_sketch_spec(k: int = 21, sketch_size: int = 100_000) -> TaskTypeSpec:
    return TaskTypeSpec(
        task_type="mash_sketch", tool_id="mash",
        build_argv=lambda p: ["mash", "sketch", "-k", str(p.get("k", k)),
                              "-s", str(p.get("sketch_size", sketch_size)),
                              "-p", str(p.get("threads", 4)), "-o", p["prefix"], p["input"]],
        validators=["file_exists", "file_nonempty"],
        default_failure_policy=FailurePolicy(retryable=True, max_retries=1, timeout_seconds=3600),
    )


def mash_paste_spec() -> TaskTypeSpec:
    return TaskTypeSpec(
        task_type="mash_paste", tool_id="mash",
        build_argv=lambda p: ["mash", "paste", p["prefix"], *p["sketches"]],
        validators=["file_exists", "file_nonempty"],
        default_failure_policy=FailurePolicy(retryable=True, max_retries=1, timeout_seconds=600),
    )


def mash_dist_spec() -> TaskTypeSpec:
    return TaskTypeSpec(
        task_type="mash_dist", tool_id="mash",
        build_argv=lambda p: ["mash", "dist", p["combined"], p["combined"]],
        validators=["file_exists", "file_nonempty", "mash_dist_matrix_valid"],
        default_failure_policy=FailurePolicy(retryable=True, max_retries=1, timeout_seconds=1200),
    )


# ---- reconstructed/non-reproducible detection + guard -----------------------

def is_reconstructed(genome_fasta: str | Path) -> bool:
    """True if a genome is flagged as a reconstructed / non-reproducible inference
    (e.g. a Cactus ancestor): a sibling NON_DETERMINISTIC_WARNING.txt, or a
    {fasta}.provenance.json with determinism.reproducible == false."""
    p = Path(genome_fasta)
    if (p.parent / "NON_DETERMINISTIC_WARNING.txt").exists():
        return True
    prov = Path(str(p) + ".provenance.json")
    if prov.exists():
        try:
            d = json.loads(prov.read_text(encoding="utf-8"))
            if d.get("determinism", {}).get("reproducible") is False:
                return True
        except (OSError, json.JSONDecodeError):
            pass
    return False


def assess_observed_taxa(reconstructed_labels: list[str]) -> CheckResult:
    """Gate the tree on observed-only taxa. Any reconstructed/non-reproducible
    genome -> FAILED (diagnostic only, NOT a phylogeny of observed taxa)."""
    if reconstructed_labels:
        return CheckResult(
            "observed_taxa_only", "FAILED",
            f"tree includes reconstructed/non-reproducible genome(s): {sorted(reconstructed_labels)}; "
            "this is a DIAGNOSTIC, not a phylogeny of observed taxa",
            {"reconstructed": sorted(reconstructed_labels)})
    return CheckResult("observed_taxa_only", "PASSED", "all taxa are observed genomes")


# ---- NJ tree from a Mash dist TSV -------------------------------------------

def nj_tree_from_mash(dist_tsv: str | Path, *, outgroup_substr: str | None = None) -> str:
    """Build a Neighbor-Joining tree (Newick) from a Mash dist TSV."""
    import csv

    import dendropy

    def short(p: str) -> str:
        return p.split("/")[-1].replace(".fa", "").replace(".fasta", "").replace(".msh", "")

    rows = [ln.rstrip("\n").split("\t") for ln in Path(dist_tsv).read_text().splitlines() if ln.strip()]
    names = sorted({short(r[0]) for r in rows})
    D = {(short(r[0]), short(r[1])): float(r[2]) for r in rows}
    tmp = Path(dist_tsv).with_suffix(".matrix.csv")
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([""] + names)
        for a in names:
            w.writerow([a] + [f"{D[(a, b)]:.6f}" for b in names])
    pdm = dendropy.PhylogeneticDistanceMatrix.from_csv(
        src=open(tmp), is_first_row_column_names=True, is_first_column_row_names=True,
        default_data_type=float, delimiter=",")
    tree = pdm.nj_tree()
    if outgroup_substr:
        og = [n for n in tree.leaf_node_iter() if outgroup_substr in n.taxon.label]
        if og:
            tree.to_outgroup_position(og[0], update_bipartitions=False)
    return tree.as_string(schema="newick").strip()


# ---- driver -----------------------------------------------------------------

def run_genome_phylogeny(
    runner: TaskRunner, *, run_id: str, genomes: dict[str, str], workdir: str | Path,
    k: int = 21, sketch_size: int = 100_000, threads: int = 4,
    outgroup: str | None = None, reconstructed: set[str] | None = None,
) -> dict[str, Any]:
    """Whole-genome alignment-free phylogeny through the harness. ``genomes`` maps
    label -> genome FASTA path. Reconstructed inputs are auto-detected (and may be
    forced via ``reconstructed``); a tree including any of them is gated out of
    BIOLOGICALLY_INTERPRETABLE."""
    workdir = Path(workdir)
    (workdir / "sketches").mkdir(parents=True, exist_ok=True)

    recon = set(reconstructed or [])
    for label, fasta in genomes.items():
        if is_reconstructed(fasta):
            recon.add(label)

    # 1) sketch each genome (one audited task per genome).
    sketches: list[str] = []
    sketch_bundles: dict[str, Any] = {}
    for label, fasta in genomes.items():
        prefix = workdir / "sketches" / label
        msh = str(prefix) + ".msh"
        task = mash_sketch_spec(k, sketch_size).build_task(
            task_id=f"{run_id}.sketch_{label}", run_id=run_id,
            params={"input": str(fasta), "prefix": str(prefix), "k": k,
                    "sketch_size": sketch_size, "threads": threads},
            inputs=[str(fasta)], outputs_expected=[msh],
            resources=ResourceRequest(cpus=threads, memory_gb=4))
        b = taskstore.run_or_resume(runner, task,
                                    limitations=["Mash sketch is a lossy k-mer summary of the genome."])
        sketch_bundles[label] = b
        if b["status_technical"] == "SUCCEEDED":
            sketches.append(msh)

    if len(sketches) < 3:
        return {"sketches": sketch_bundles, "dist": None, "tree_path": None,
                "error": "need >=3 successful sketches"}

    # 2) paste into one combined sketch.
    combined_prefix = workdir / "combined"
    combined = str(combined_prefix) + ".msh"
    paste = mash_paste_spec().build_task(
        task_id=f"{run_id}.paste", run_id=run_id,
        params={"prefix": str(combined_prefix), "sketches": sketches},
        inputs=sketches, outputs_expected=[combined], resources=ResourceRequest(memory_gb=2))
    taskstore.run_or_resume(runner, paste)

    # 3) distance matrix (stdout captured faithfully) + the science gate.
    dist_tsv = workdir / "mash_dist.tsv"

    def hook(_t: Task, _o: list[str]) -> list[CheckResult]:
        checks: list[CheckResult] = [assess_observed_taxa(sorted(recon))]
        try:
            nwk = nj_tree_from_mash(dist_tsv, outgroup_substr=outgroup)
            (workdir / "tree.nwk").write_text(nwk + "\n", encoding="utf-8")
            n = nwk.count(",") + 1
            checks.append(CheckResult("nj_tree_built", "PASSED", f"NJ tree with {n} taxa",
                                      {"n_taxa": n}))
        except Exception as exc:  # noqa: BLE001
            checks.append(CheckResult("nj_tree_built", "FAILED", f"NJ failed: {exc}"))
        return checks

    extra = list(GENOME_PHYLO_NOT_ALLOWED)
    if recon:
        extra.append("Reconstructed/non-reproducible genomes are present; their placement is a "
                     "diagnostic of the reconstruction, NOT evidence of a real biological ancestor.")
    dist = mash_dist_spec().build_task(
        task_id=f"{run_id}.dist", run_id=run_id,
        params={"combined": combined, "stdout_to": str(dist_tsv)},
        inputs=[combined], outputs_expected=[str(dist_tsv)], resources=ResourceRequest(memory_gb=2))
    dist_bundle = taskstore.run_or_resume(
        runner, dist, statistical_evidence_hook=hook,
        allowed=["A whole-genome Mash distance matrix and a Neighbor-Joining tree were produced."],
        limitations=[f"{len(genomes)} genomes; reconstructed/non-reproducible: {sorted(recon)}."],
        extra_not_allowed=extra)

    return {
        "sketches": sketch_bundles,
        "dist": dist_bundle,
        "dist_tsv": str(dist_tsv),
        "tree_path": str(workdir / "tree.nwk"),
        "reconstructed": sorted(recon),
        "scientific_state": dist_bundle["status_scientific"],
    }
