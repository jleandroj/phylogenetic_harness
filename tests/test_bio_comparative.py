"""Real comparative slice: RAxML bootstrap trees + gene-tree discordance (RF).

The deterministic parts (RF comparison, bootstrap evidence parsing) always run;
the end-to-end MAFFT+RAxML part skips if the tools are absent."""
import shutil

import pytest

from harness.bio import (
    compare_gene_trees,
    raxml_bootstrap_evidence,
    run_comparative_slice,
)

# ---- deterministic: gene-tree comparison (RF) ----

def test_identical_trees_are_congruent(tmp_path):
    nwk = "((t0,t1),(t2,t3),t4);"
    a = tmp_path / "a.nwk"
    a.write_text(nwk)
    b = tmp_path / "b.nwk"
    b.write_text(nwk)
    cmp = compare_gene_trees(a, b)
    assert cmp["rf_distance"] == 0
    assert cmp["congruent"] is True
    assert cmp["n_taxa"] == 5


def test_different_topologies_are_discordant(tmp_path):
    a = tmp_path / "a.nwk"
    a.write_text("((t0,t1),(t2,t3),t4);")
    b = tmp_path / "b.nwk"
    b.write_text("((t0,t2),(t1,t3),t4);")
    cmp = compare_gene_trees(a, b)
    assert cmp["rf_distance"] > 0
    assert cmp["congruent"] is False
    assert 0.0 < cmp["normalized_rf"] <= 1.0


# ---- deterministic: bootstrap evidence parsing ----

def test_raxml_bootstrap_evidence_passes_when_strong(tmp_path):
    t = tmp_path / "bip.nwk"
    t.write_text("((a:0.1,b:0.1)95:0.2,(c:0.1,d:0.1)88:0.2,e:0.1);")
    ev = raxml_bootstrap_evidence(t)
    assert ev[0].name == "raxml_mean_bootstrap"
    assert ev[0].passed                       # mean (95,88) >= 70
    assert ev[0].data["mean_bootstrap"] == pytest.approx(91.5)


def test_raxml_bootstrap_weak_is_not_applicable(tmp_path):
    t = tmp_path / "bip.nwk"
    t.write_text("((a:0.1,b:0.1)20:0.2,(c:0.1,d:0.1)30:0.2,e:0.1);")
    ev = raxml_bootstrap_evidence(t)
    assert ev[0].status == "NOT_APPLICABLE"   # weak support: insufficient, not contradicting


# ---- end-to-end: two genes, MAFFT + RAxML bootstrap, discordance reported ----

@pytest.mark.skipif(
    not (shutil.which("mafft") and shutil.which("raxmlHPC")),
    reason="mafft and/or raxmlHPC not installed",
)
def test_comparative_two_genes(runner_factory, tmp_path):
    runner, _, tools = runner_factory()
    assert tools.get("raxmlHPC").available

    # Two genes over the same 6 taxa but with DIFFERENT grouping signals.
    def gene(muts):
        base = list("ACGT" * 18)  # 72 bp
        taxa = {f"taxon_{i}": list(base) for i in range(6)}
        for col, (grpA, grpB) in muts.items():
            for i in grpA:
                taxa[f"taxon_{i}"][col] = "A"
            for i in grpB:
                taxa[f"taxon_{i}"][col] = "T"
        return "".join(f">{n}\n{''.join(s)}\n" for n, s in taxa.items())

    g1 = tmp_path / "geneA.fasta"
    g1.write_text(gene({8: ([0, 1, 2], [3, 4, 5]), 20: ([0, 1, 2], [3, 4, 5]),
                        32: ([0, 1], [2, 3]), 44: ([0, 1, 2], [3, 4, 5]), 56: ([0, 2], [1, 3])}))
    g2 = tmp_path / "geneB.fasta"
    g2.write_text(gene({8: ([0, 1, 3], [2, 4, 5]), 20: ([0, 1, 3], [2, 4, 5]),
                        32: ([0, 3], [1, 2]), 44: ([0, 1, 3], [2, 4, 5]), 56: ([4, 5], [0, 1])}))

    out = run_comparative_slice(runner, run_id="cmp", genes={"geneA": str(g1), "geneB": str(g2)},
                                workdir=tmp_path / "work", nboot=50)

    # Both genes produced a real bootstrap tree.
    for name in ("geneA", "geneB"):
        g = out["genes"][name]
        assert g["tree"]["status_technical"] == "SUCCEEDED"
        assert g["seed"]  # deterministic seed wired from the seed manager
        # Interpretability comes from real evidence (alignment + maybe bootstrap).
        assert g["tree"]["status_scientific"] in ("BIOLOGICALLY_INTERPRETABLE", "LOW_CONFIDENCE")

    # Discordance is COMPUTED and REPORTED (a real RF value), not assumed away.
    assert len(out["comparisons"]) == 1
    cmp = out["comparisons"][0]
    assert cmp["pair"] == ["geneA", "geneB"]
    assert isinstance(cmp["rf_distance"], int) and cmp["rf_distance"] >= 0
    assert "discordant" in out and "note" in out
