"""Real end-to-end comparative pipeline: per-gene model selection (IQ-TREE
ModelFinder) + ASTRAL coalescent species tree + gene-tree discordance.

Skips cleanly when the tools are absent; runs for real when present (the
HARNESS_TOOL_PATHS env / phylo_extra conda env provides iqtree + astral)."""
import shutil

import pytest

from harness.bio import run_phylogenomic_pipeline
from harness.validators import newick_valid

HAVE_IQTREE = bool(shutil.which("iqtree"))
HAVE_ASTRAL = bool(shutil.which("astral"))


def _gene(muts, length=90):
    base = list(("ACGT" * ((length // 4) + 1))[:length])
    taxa = {f"taxon_{i}": list(base) for i in range(6)}
    for col, (grpA, grpB) in muts.items():
        for i in grpA:
            taxa[f"taxon_{i}"][col] = "A"
        for i in grpB:
            taxa[f"taxon_{i}"][col] = "T"
    return "".join(f">{n}\n{''.join(s)}\n" for n, s in taxa.items())


def _two_genes(tmp_path):
    g1 = tmp_path / "geneA.fasta"
    g1.write_text(_gene({8: ([0, 1, 2], [3, 4, 5]), 22: ([0, 1, 2], [3, 4, 5]),
                        40: ([0, 1], [2, 3]), 58: ([0, 1, 2], [3, 4, 5]), 74: ([0, 2], [1, 3])}))
    g2 = tmp_path / "geneB.fasta"
    g2.write_text(_gene({8: ([0, 1, 3], [2, 4, 5]), 22: ([0, 1, 3], [2, 4, 5]),
                        40: ([0, 3], [1, 2]), 58: ([0, 1, 3], [2, 4, 5]), 74: ([4, 5], [0, 1])}))
    return {"geneA": str(g1), "geneB": str(g2)}


@pytest.mark.skipif(not (shutil.which("mafft") and HAVE_IQTREE),
                    reason="mafft and/or iqtree not installed")
def test_model_selection_pipeline(runner_factory, tmp_path):
    runner, _, tools = runner_factory()
    assert tools.get("iqtree").available
    out = run_phylogenomic_pipeline(
        runner, run_id="pl", genes=_two_genes(tmp_path), workdir=tmp_path / "work",
        nboot=1000, model_selection=True, species_tree=HAVE_ASTRAL,
    )
    assert out["model_selection"] is True
    for name in ("geneA", "geneB"):
        g = out["genes"][name]
        assert g["method"] == "iqtree_mfp"
        assert g["tree"]["status_technical"] == "SUCCEEDED"
        # ModelFinder actually selected a model — the bootstrap used it.
        assert g["model"], "no model selected"
        assert newick_valid(g["tree_path"]).passed
    # Discordance is computed and reported.
    assert len(out["comparisons"]) == 1
    assert isinstance(out["comparisons"][0]["rf_distance"], int)


@pytest.mark.skipif(not (shutil.which("mafft") and HAVE_IQTREE and HAVE_ASTRAL),
                    reason="mafft/iqtree/astral not all installed")
def test_astral_species_tree(runner_factory, tmp_path):
    runner, _, _ = runner_factory()
    out = run_phylogenomic_pipeline(
        runner, run_id="pl", genes=_two_genes(tmp_path), workdir=tmp_path / "work",
        nboot=1000, model_selection=True, species_tree=True,
    )
    sp = out["species_tree"]
    assert sp is not None and sp["species"]["status_technical"] == "SUCCEEDED"
    assert newick_valid(sp["species_path"]).passed
    # Species tree vs each gene tree is reported (RF), including the few-loci caveat.
    assert len(sp["vs_genes"]) == 2
    not_allowed = sp["species"]["interpretation"]["interpretation_not_allowed"]
    assert any("ILS" in s or "coalescent" in s.lower() or "low power" in s.lower() for s in not_allowed)


def test_pipeline_skips_species_tree_when_astral_absent(runner_factory, tmp_path, monkeypatch):
    """If ASTRAL is not available, the pipeline reports it skipped — never fakes a
    species tree. (Forced by pretending astral is unavailable.)"""
    runner, _, tools = runner_factory()
    if "astral" in tools.all():
        tools.get("astral").available = False
    if not shutil.which("mafft"):
        pytest.skip("mafft not installed")
    if not (HAVE_IQTREE or shutil.which("raxmlHPC")):
        pytest.skip("no tree tool")
    out = run_phylogenomic_pipeline(
        runner, run_id="pl", genes=_two_genes(tmp_path), workdir=tmp_path / "work",
        nboot=100, model_selection=HAVE_IQTREE, species_tree=True,
    )
    sp = out["species_tree"]
    assert sp is not None and sp.get("skipped")
