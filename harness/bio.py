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


def raxml_bootstrap_evidence(newick: str | Path, *, min_mean_support: float = 70.0) -> list[CheckResult]:
    """Mean bootstrap support (0-100) from a RAxML bipartitions tree — REAL
    statistical evidence (classical bootstrap, not SH-like)."""
    try:
        import dendropy
        tree = dendropy.Tree.get(path=str(newick), schema="newick", preserve_underscores=True)
    except Exception as exc:  # noqa: BLE001
        return [CheckResult("raxml_tree_parse", "FAILED", f"could not parse tree: {exc}")]
    supports = []
    for node in tree.internal_nodes():
        if node.label:
            try:
                supports.append(float(node.label))
            except ValueError:
                pass
    if not supports:
        return [CheckResult("raxml_bootstrap", "NOT_APPLICABLE", "no bootstrap values")]
    mean = sum(supports) / len(supports)
    # >=70% mean bootstrap is the conventional "well-supported" threshold; below
    # that it is insufficient support (NOT_APPLICABLE), not a contradiction.
    status = "PASSED" if mean >= min_mean_support else "NOT_APPLICABLE"
    return [CheckResult("raxml_mean_bootstrap", status,
                        f"mean bootstrap {mean:.1f}% over {len(supports)} nodes",
                        {"mean_bootstrap": round(mean, 2), "n_nodes": len(supports)})]


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


def raxml_tree_spec() -> TaskTypeSpec:
    """RAxML rapid bootstrap + ML search. Writes RAxML_bipartitions.<name> in -w;
    not retryable because RAxML refuses to overwrite its own output files."""
    return TaskTypeSpec(
        task_type="tree_raxml", tool_id="raxmlHPC",
        build_argv=lambda p: [
            "raxmlHPC", "-f", "a", "-x", str(p["seed"]), "-p", str(p["seed"]),
            "-N", str(p.get("nboot", 100)), "-m", p.get("model", "GTRGAMMA"),
            "-s", p["input"], "-n", p["name"], "-w", p["workdir"],
        ],
        validators=["newick_valid"],
        default_failure_policy=FailurePolicy(retryable=False, max_retries=0, timeout_seconds=1800),
    )


PHYLO_NOT_ALLOWED = [
    "This tree is one estimate under one model and alignment; it is not THE true tree.",
    "Local SH-like supports are not classical bootstrap and do not prove a clade.",
    "Gene-tree topology may differ from the species tree (ILS / introgression).",
]


def compare_gene_trees(tree_a: str | Path, tree_b: str | Path) -> dict[str, Any]:
    """Robinson-Foulds comparison of two gene trees on a shared taxon namespace.

    Returns the RF distance, its normalisation, and whether the topologies are
    congruent. Discordance is REPORTED, never assumed away — it is the honest
    signal of ILS / introgression / gene-specific history.
    """
    import dendropy
    from dendropy.calculate import treecompare

    tns = dendropy.TaxonNamespace()
    ta = dendropy.Tree.get(path=str(tree_a), schema="newick", taxon_namespace=tns,
                           preserve_underscores=True)
    tb = dendropy.Tree.get(path=str(tree_b), schema="newick", taxon_namespace=tns,
                           preserve_underscores=True)
    ta.encode_bipartitions()
    tb.encode_bipartitions()
    rf = treecompare.symmetric_difference(ta, tb)
    n_taxa = len(tns)
    max_rf = max(1, 2 * (n_taxa - 3))  # unrooted binary upper bound
    return {
        "rf_distance": rf,
        "max_rf": max_rf,
        "normalized_rf": round(rf / max_rf, 4),
        "congruent": rf == 0,
        "n_taxa": n_taxa,
    }


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


def run_raxml_tree(
    runner: TaskRunner, *, run_id: str, aligned: str | Path, workdir: str | Path,
    name: str, nboot: int = 100,
) -> dict[str, Any]:
    """RAxML rapid-bootstrap ML tree with REAL bootstrap evidence. The seed is
    derived deterministically from the seed manager (reproducibility)."""
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    seed = runner.seeds.derive(run_id, "raxml", name) % 1_000_000 if runner.seeds else 12345
    bip = workdir / f"RAxML_bipartitions.{name}"

    task = raxml_tree_spec().build_task(
        task_id=f"{run_id}.raxml_{name}", run_id=run_id,
        params={"input": str(Path(aligned).resolve()), "name": name, "seed": seed,
                "nboot": nboot, "workdir": str(workdir)},
        inputs=[str(aligned)], outputs_expected=[str(bip)],
        resources=ResourceRequest(cpus=2, memory_gb=2),
    )

    def hook(_t: Task, _o: list[str]) -> list[CheckResult]:
        return alignment_evidence(aligned) + raxml_bootstrap_evidence(bip)

    bundle = runner.run_task(
        task, statistical_evidence_hook=hook,
        allowed=["RAxML inferred an ML gene tree with classical bootstrap support."],
        limitations=["Bootstrap is conditional on the model (GTRGAMMA) and the alignment."],
    )
    return {"tree": bundle, "tree_path": str(bip), "seed": seed}


def run_comparative_slice(
    runner: TaskRunner, *, run_id: str, genes: dict[str, str], workdir: str | Path,
    nboot: int = 100,
) -> dict[str, Any]:
    """Align + RAxML-bootstrap a tree for EACH gene, then compare topologies
    (gene-tree discordance). Returns per-gene bundles + pairwise RF comparisons."""
    workdir = Path(workdir)
    per_gene: dict[str, Any] = {}
    for name, fasta in genes.items():
        gdir = workdir / name
        aligned = gdir / "aligned.fasta"
        msa = msa_mafft_spec().build_task(
            task_id=f"{run_id}.msa_{name}", run_id=run_id,
            params={"input": str(fasta), "stdout_to": str(aligned)},
            inputs=[str(fasta)], outputs_expected=[str(aligned)],
            resources=ResourceRequest(cpus=2, memory_gb=2),
        )
        gdir.mkdir(parents=True, exist_ok=True)
        msa_bundle = runner.run_task(msa, limitations=["Alignment quality bounds the gene tree."])
        if msa_bundle["status_technical"] != "SUCCEEDED":
            per_gene[name] = {"msa": msa_bundle, "tree": None, "tree_path": None}
            continue
        rax = run_raxml_tree(runner, run_id=run_id, aligned=aligned, workdir=gdir / "raxml",
                             name=name, nboot=nboot)
        per_gene[name] = {"msa": msa_bundle, **rax}

    # Pairwise topology comparison across genes whose trees succeeded.
    built = {n: g["tree_path"] for n, g in per_gene.items()
             if g.get("tree") and g["tree"]["status_technical"] == "SUCCEEDED"}
    comparisons = []
    names = sorted(built)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            cmp = compare_gene_trees(built[a], built[b])
            cmp["pair"] = [a, b]
            comparisons.append(cmp)

    any_discordant = any(not c["congruent"] for c in comparisons)
    return {
        "genes": per_gene,
        "comparisons": comparisons,
        "discordant": any_discordant,
        # Honest interpretation: discordance is expected biology, not an error.
        "note": ("Gene trees disagree (RF>0): consistent with ILS/introgression/"
                 "gene-specific history — NOT evidence of a single true tree."
                 if any_discordant else
                 "Gene trees are congruent on these data; congruence is not proof of the species tree."),
    }
