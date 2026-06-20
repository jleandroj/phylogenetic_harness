"""Audit round 4: scientific guardrails — locus independence + supported conflict.
These make the harness's scepticism bite on the SCIENCE, not just the plumbing."""
from harness.phylo_guards import (
    assess_locus_independence,
    incompatible,
    supported_conflict,
)

# ---- locus independence gate ----

def test_declared_independent_passes():
    c = assess_locus_independence(True, all_gene_trees_congruent=False, n_loci=10)
    assert c.passed


def test_declared_linked_fails():
    c = assess_locus_independence(False, all_gene_trees_congruent=False, n_loci=6)
    assert c.status == "FAILED" and "coalescent" in c.detail.lower()


def test_undeclared_all_congruent_fails_like_mt():
    """The exact ape-mitochondrial case: 6 congruent gene trees, independence not
    declared -> FAILED (linked-loci signature). The harness must NOT call this a
    coalescent species tree."""
    c = assess_locus_independence(None, all_gene_trees_congruent=True, n_loci=6)
    assert c.status == "FAILED"
    assert "linked" in c.detail.lower() or "mitochondrial" in c.detail.lower()


def test_undeclared_with_discordance_is_not_applicable():
    c = assess_locus_independence(None, all_gene_trees_congruent=False, n_loci=6)
    assert c.status == "NOT_APPLICABLE"


# ---- clade incompatibility + supported conflict ----

def test_incompatible_clades():
    assert incompatible(frozenset("AB"), frozenset("BC"))      # overlap, neither subset
    assert not incompatible(frozenset("AB"), frozenset("ABC"))  # nested
    assert not incompatible(frozenset("AB"), frozenset("CD"))   # disjoint


def test_supported_conflict_only_counts_strong_clades(tmp_path):
    # Two trees with INCOMPATIBLE clades, both strongly supported -> real conflict.
    a = tmp_path / "a.nwk"
    a.write_text("((A:0.1,B:0.1)99:0.2,(C:0.1,D:0.1)99:0.2,E:0.1);")
    b = tmp_path / "b.nwk"
    b.write_text("((A:0.1,C:0.1)99:0.2,(B:0.1,D:0.1)99:0.2,E:0.1);")
    sc = supported_conflict(a, b, min_support=95)
    assert sc["has_supported_conflict"] is True


def test_weak_conflict_is_not_real_discordance(tmp_path):
    # Same incompatible topologies but WEAKLY supported -> not counted (noise).
    a = tmp_path / "a.nwk"
    a.write_text("((A:0.1,B:0.1)40:0.2,(C:0.1,D:0.1)35:0.2,E:0.1);")
    b = tmp_path / "b.nwk"
    b.write_text("((A:0.1,C:0.1)42:0.2,(B:0.1,D:0.1)38:0.2,E:0.1);")
    sc = supported_conflict(a, b, min_support=95)
    assert sc["has_supported_conflict"] is False   # estimation error, not ILS


def test_congruent_trees_no_conflict(tmp_path):
    nwk = "((A:0.1,B:0.1)99:0.2,(C:0.1,D:0.1)99:0.2,E:0.1);"
    a = tmp_path / "a.nwk"
    a.write_text(nwk)
    b = tmp_path / "b.nwk"
    b.write_text(nwk)
    assert supported_conflict(a, b, min_support=95)["has_supported_conflict"] is False
