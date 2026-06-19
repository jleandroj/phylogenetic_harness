"""First real phylogenomics slice: MAFFT alignment -> FastTree tree.

This is the vertical slice that makes "biologically interpretable" mean something:
real tools run through the single TaskRunner, and a ``statistical_evidence_hook``
computes REAL evidence from the produced files (alignment quality + tree support)
that the science layer uses to decide the scientific state. The standing
phylogenetic prohibitions (no single 'true' tree, an inferred ancestor is not a
real individual, ILS/recombination caveats) remain attached regardless.

Tools used (registered contracts): mafft, fasttree. Both write their result to
stdout, captured faithfully to the declared output via the executor's
``stdout_to`` (argv-only, no shell redirection).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .runner import TaskRunner
from .task_types import TaskTypeSpec
from .tasks import FailurePolicy, ResourceRequest, Task
from .validators import CheckResult, _parse_fasta

GAP_CHARS = set("-.")


# ---- evidence from the produced files (real statistics) ---------------------

def _columns(seqs: list[str]) -> list[str]:
    width = len(seqs[0])
    return ["".join(s[i] for s in seqs) for i in range(width)]


def alignment_evidence(aligned_fasta: str | Path) -> list[CheckResult]:
    """Compute real alignment statistics as statistical-evidence checks."""
    names, lengths, _ = _parse_fasta(Path(aligned_fasta))
    seqs_by_name: dict[str, str] = {}
    # Re-read sequences (parse_fasta only returns lengths); read full bodies.
    current = None
    buf: dict[str, list[str]] = {}
    with open(aligned_fasta, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith(">"):
                current = line[1:].split()[0] if line[1:].split() else ""
                buf[current] = []
            elif current is not None:
                buf[current].append(line)
    seqs_by_name = {k: "".join(v) for k, v in buf.items()}
    seqs = list(seqs_by_name.values())
    n = len(seqs)
    if n == 0 or len(set(len(s) for s in seqs)) != 1:
        return [CheckResult("aln_is_alignment", "FAILED", "sequences not equal length")]

    cols = _columns(seqs)
    total_cells = sum(len(s) for s in seqs)
    gaps = sum(c in GAP_CHARS for s in seqs for c in s)
    gap_fraction = gaps / total_cells if total_cells else 1.0

    variable = 0
    informative = 0
    for col in cols:
        residues = [c.upper() for c in col if c not in GAP_CHARS and c.upper() != "N"]
        distinct = set(residues)
        if len(distinct) > 1:
            variable += 1
            counts = {r: residues.count(r) for r in distinct}
            if sum(1 for v in counts.values() if v >= 2) >= 2:
                informative += 1

    return [
        CheckResult("aln_min_sequences", "PASSED" if n >= 4 else "FAILED", f"{n} sequences",
                    {"n_sequences": n}),
        CheckResult("aln_variable_sites", "PASSED" if variable > 0 else "FAILED",
                    f"{variable} variable columns", {"variable_sites": variable}),
        CheckResult("aln_parsimony_informative", "PASSED" if informative >= 1 else "FAILED",
                    f"{informative} informative columns", {"informative_sites": informative}),
        CheckResult("aln_gap_fraction", "PASSED" if gap_fraction < 0.5 else "FAILED",
                    f"gap fraction {gap_fraction:.3f}", {"gap_fraction": round(gap_fraction, 4)}),
    ]


def tree_support_evidence(newick: str | Path) -> list[CheckResult]:
    """Mean internal-node support from the inferred tree (FastTree SH-like supports)."""
    try:
        import dendropy
        tree = dendropy.Tree.get(path=str(newick), schema="newick", preserve_underscores=True)
    except Exception as exc:  # noqa: BLE001
        return [CheckResult("tree_parse", "FAILED", f"could not parse tree: {exc}")]
    supports = []
    for node in tree.internal_nodes():
        if node.label:
            try:
                supports.append(float(node.label))
            except ValueError:
                pass
    if not supports:
        return [CheckResult("tree_support", "NOT_APPLICABLE", "no internal support values")]
    mean = sum(supports) / len(supports)
    # High support is positive evidence; low support is NOT a contradiction of the
    # alignment signal — it is simply insufficient support, recorded as
    # NOT_APPLICABLE so it neither inflates nor sinks the verdict on its own.
    status = "PASSED" if mean >= 0.7 else "NOT_APPLICABLE"
    return [CheckResult("tree_mean_support", status,
                        f"mean support {mean:.3f} over {len(supports)} nodes",
                        {"mean_support": round(mean, 4), "n_nodes": len(supports)})]


# ---- task-type specs --------------------------------------------------------

def msa_mafft_spec() -> TaskTypeSpec:
    return TaskTypeSpec(
        task_type="msa_mafft", tool_id="mafft",
        build_argv=lambda p: ["mafft", "--auto", p["input"]],
        validators=["alignment_valid"],
        default_failure_policy=FailurePolicy(retryable=True, max_retries=1, timeout_seconds=600),
    )


def tree_fasttree_spec() -> TaskTypeSpec:
    return TaskTypeSpec(
        task_type="tree_fasttree", tool_id="fasttree",
        build_argv=lambda p: ["fasttree", "-nt", p["input"]],
        validators=["newick_valid"],
        default_failure_policy=FailurePolicy(retryable=True, max_retries=1, timeout_seconds=600),
    )


PHYLO_NOT_ALLOWED = [
    "This tree is one estimate under one model and alignment; it is not THE true tree.",
    "Local SH-like supports are not classical bootstrap and do not prove a clade.",
    "Gene-tree topology may differ from the species tree (ILS / introgression).",
]


def run_phylo_slice(
    runner: TaskRunner, *, run_id: str, fasta_path: str | Path, workdir: str | Path,
) -> dict[str, Any]:
    """Align with MAFFT then infer a tree with FastTree, with real evidence feeding
    the science layer. Returns both task bundles."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    aligned = workdir / "aligned.fasta"
    tree = workdir / "tree.nwk"

    msa = msa_mafft_spec().build_task(
        task_id=f"{run_id}.msa", run_id=run_id,
        params={"input": str(fasta_path), "stdout_to": str(aligned)},
        inputs=[str(fasta_path)], outputs_expected=[str(aligned)],
        resources=ResourceRequest(cpus=2, memory_gb=2),
    )
    msa_bundle = runner.run_task(
        msa,
        allowed=["MAFFT produced a multiple sequence alignment (equal-length columns)."],
        limitations=["Alignment quality bounds every downstream phylogenetic claim."],
    )
    if msa_bundle["status_technical"] != "SUCCEEDED":
        return {"msa": msa_bundle, "tree": None}

    def hook(_task: Task, _outputs: list[str]) -> list[CheckResult]:
        return alignment_evidence(aligned) + tree_support_evidence(tree)

    tree_task = tree_fasttree_spec().build_task(
        task_id=f"{run_id}.tree", run_id=run_id,
        params={"input": str(aligned), "stdout_to": str(tree)},
        inputs=[str(aligned)], outputs_expected=[str(tree)],
        resources=ResourceRequest(cpus=2, memory_gb=2),
    )
    tree_bundle = runner.run_task(
        tree_task,
        statistical_evidence_hook=hook,
        allowed=["FastTree inferred an approximate-ML gene tree from the alignment."],
        limitations=["Approximate ML; supports are SH-like, not bootstrap."],
    )
    return {"msa": msa_bundle, "tree": tree_bundle}
