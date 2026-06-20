"""Scientific guardrails for the comparative pipeline (audit round 4).

Two checks the harness must make so it does not overclaim — the exact failures a
sceptical phylogenomics harness exists to catch:

1. Locus independence (assess_locus_independence): ASTRAL and the multispecies
   coalescent assume INDEPENDENT loci. Running it on linked loci (e.g. several
   mitochondrial genes — one non-recombining molecule = ONE effective locus) is
   methodologically invalid. If independence is declared false, or undeclared and
   the gene trees are fully congruent (the signature of linked loci), the species
   tree must NOT be called a coalescent estimate.

2. Supported discordance (supported_conflict): raw Robinson-Foulds counts every
   topological difference, including those driven by gene-tree ESTIMATION ERROR
   on short alignments. Real ILS/introgression shows up as conflict between clades
   that are WELL SUPPORTED in both trees. We only call a pair discordant when an
   incompatible clade is supported >= threshold in each tree.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .validators import CheckResult


def clades_with_support(newick: str | Path) -> tuple[list[tuple[frozenset[str], float | None]], set[str]]:
    """Return [(leaf-set of each non-trivial clade, support)], plus all taxa."""
    import dendropy
    tree = dendropy.Tree.get(path=str(newick), schema="newick", preserve_underscores=True)
    all_leaves = {leaf.taxon.label for leaf in tree.leaf_node_iter() if leaf.taxon}
    clades: list[tuple[frozenset[str], float | None]] = []
    for node in tree.preorder_internal_node_iter():
        leaves = frozenset(leaf.taxon.label for leaf in node.leaf_iter() if leaf.taxon)
        if 1 < len(leaves) < len(all_leaves):  # skip root and trivial clades
            sup: float | None = None
            if node.label:
                try:
                    sup = float(node.label)
                except ValueError:
                    sup = None
            clades.append((leaves, sup))
    return clades, all_leaves


def incompatible(a: frozenset[str], b: frozenset[str]) -> bool:
    """Two clades (over a shared taxon set) conflict iff they overlap but neither
    contains the other (and they are not disjoint)."""
    if a.isdisjoint(b):
        return False
    if a <= b or b <= a:
        return False
    return True


def supported_conflict(
    tree_a: str | Path, tree_b: str | Path, *, min_support: float,
) -> dict[str, Any]:
    """Is there an incompatible clade supported >= min_support in BOTH trees?"""
    ca, _ = clades_with_support(tree_a)
    cb, _ = clades_with_support(tree_b)
    conflicts = []
    for A, sa in ca:
        if sa is None or sa < min_support:
            continue
        for B, sb in cb:
            if sb is None or sb < min_support:
                continue
            if incompatible(A, B):
                conflicts.append((sorted(A), sorted(B)))
    return {
        "has_supported_conflict": bool(conflicts),
        "n_supported_conflicts": len(conflicts),
        "min_support": min_support,
        "examples": conflicts[:3],
    }


def assess_locus_independence(
    loci_independent: bool | None, *, all_gene_trees_congruent: bool, n_loci: int,
) -> CheckResult:
    """Gate ASTRAL on locus independence (audit round 4, Q15).

    PASSED only when independence is asserted. FAILED when loci are declared
    non-independent, or undeclared AND fully congruent (the linked-loci
    signature) — in which case the species tree is not a valid coalescent
    estimate. NOT_APPLICABLE when undeclared but discordance is present (consistent
    with, but not proof of, independence).
    """
    if loci_independent is True:
        return CheckResult("loci_independent", "PASSED",
                           f"{n_loci} loci asserted independent", {"declared": True})
    if loci_independent is False:
        return CheckResult("loci_independent", "FAILED",
                           "loci declared NON-independent (linked); ASTRAL's coalescent "
                           "assumption is violated — this is not a species-tree estimate",
                           {"declared": False})
    # Undeclared.
    if all_gene_trees_congruent:
        return CheckResult("loci_independent", "FAILED",
                           "independence not established and all gene trees are congruent "
                           "(the signature of linked loci, e.g. mitochondrial); cannot be "
                           "treated as a coalescent species-tree estimate",
                           {"declared": None, "all_congruent": True})
    return CheckResult("loci_independent", "NOT_APPLICABLE",
                       "independence not established; observed discordance is consistent with "
                       "(but does not prove) independent loci",
                       {"declared": None, "all_congruent": False})
