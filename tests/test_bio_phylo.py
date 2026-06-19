"""Real phylogenomics slice: MAFFT alignment -> FastTree tree, with real evidence
feeding the science layer. Skips cleanly if the tools are absent."""
import shutil

import pytest

from harness.bio import alignment_evidence, run_phylo_slice
from harness.validators import alignment_valid, newick_valid

pytestmark = pytest.mark.skipif(
    not (shutil.which("mafft") and shutil.which("fasttree")),
    reason="mafft and/or fasttree not installed",
)


def _multifasta(tmp_path):
    """6 taxa, 60 bp, identical except engineered SNPs that create several
    parsimony-informative columns (so the alignment carries real signal)."""
    base = list("ACGT" * 15)
    taxa = {f"taxon_{i}": list(base) for i in range(6)}
    # informative columns: two groups of >=2 share a variant.
    for col, (grpA, grpB) in {
        10: ([0, 1, 2], [3, 4, 5]),
        20: ([0, 1], [2, 3]),
        30: ([0, 1, 2], [3, 4, 5]),
        45: ([0, 2, 4], [1, 3, 5]),
    }.items():
        for i in grpA:
            taxa[f"taxon_{i}"][col] = "A"
        for i in grpB:
            taxa[f"taxon_{i}"][col] = "T"
    fa = tmp_path / "gene.fasta"
    fa.write_text("".join(f">{name}\n{''.join(seq)}\n" for name, seq in taxa.items()), encoding="utf-8")
    return fa


def test_alignment_evidence_on_known_alignment(tmp_path):
    fa = _multifasta(tmp_path)  # already equal length = a trivial alignment
    ev = {c.name: c for c in alignment_evidence(fa)}
    assert ev["aln_min_sequences"].passed          # 6 >= 4
    assert ev["aln_variable_sites"].passed
    assert ev["aln_parsimony_informative"].passed  # engineered informative columns


def test_phylo_slice_end_to_end(runner_factory, tmp_path):
    runner, events, tools = runner_factory()
    assert tools.get("mafft").available and tools.get("fasttree").available
    fa = _multifasta(tmp_path)

    out = run_phylo_slice(runner, run_id="phy", fasta_path=fa, workdir=tmp_path / "work")

    # MSA ran and produced a real alignment.
    assert out["msa"]["status_technical"] == "SUCCEEDED"
    aligned = tmp_path / "work" / "aligned.fasta"
    assert aligned.exists() and alignment_valid(aligned).passed

    # Tree ran and produced a valid Newick.
    tree_b = out["tree"]
    assert tree_b is not None and tree_b["status_technical"] == "SUCCEEDED"
    tree = tmp_path / "work" / "tree.nwk"
    assert newick_valid(tree).passed

    # Real evidence made the result biologically interpretable (>=2 evidences),
    # NOT because exit code was 0.
    assert tree_b["status_scientific"] == "BIOLOGICALLY_INTERPRETABLE"
    assert tree_b["degenerate"] is False

    # The standing prohibitions are still attached — no overclaiming.
    not_allowed = tree_b["interpretation"]["interpretation_not_allowed"]
    assert any("true" in s.lower() for s in not_allowed)
    assert any("ancestral" in s.lower() or "species tree" in s.lower() for s in not_allowed)


def test_phylo_slice_records_lifecycle(runner_factory, tmp_path):
    runner, events, _ = runner_factory()
    fa = _multifasta(tmp_path)
    run_phylo_slice(runner, run_id="phy", fasta_path=fa, workdir=tmp_path / "work")
    names = [e["event"] for e in events.read()]
    assert names.count("task_succeeded") == 2      # MSA + tree both succeeded
    assert "validation_succeeded" in names
