"""Whole-genome phylogenomics inside the harness: Mash specs, dist-matrix
validator, NJ tree, and the OBSERVED-TAXA-ONLY guard (the HomoPan lesson)."""
import shutil

import pytest

from harness.genome_phylo import (
    assess_observed_taxa,
    is_reconstructed,
    mash_dist_spec,
    mash_sketch_spec,
    nj_tree_from_mash,
)
from harness.validators import mash_dist_matrix_valid

# ---- specs build correct argv ----

def test_mash_sketch_argv():
    spec = mash_sketch_spec(k=21, sketch_size=1000)
    argv = spec.build_argv({"input": "g.fa", "prefix": "out/g", "k": 21,
                            "sketch_size": 1000, "threads": 4})
    assert argv[:2] == ["mash", "sketch"]
    assert "g.fa" in argv and "out/g" in argv and "1000" in argv


def test_mash_dist_validators_include_matrix_check():
    assert "mash_dist_matrix_valid" in mash_dist_spec().validators


# ---- distance-matrix validator ----

def test_dist_matrix_valid_passes(tmp_path):
    p = tmp_path / "d.tsv"
    p.write_text("a\ta\t0\t0\t1\na\tb\t0.1\t0\t1\nb\ta\t0.1\t0\t1\nb\tb\t0\t0\t1\n")
    assert mash_dist_matrix_valid(p).passed


def test_dist_matrix_asymmetric_fails(tmp_path):
    p = tmp_path / "d.tsv"
    p.write_text("a\tb\t0.1\t0\t1\nb\ta\t0.9\t0\t1\n")
    assert not mash_dist_matrix_valid(p).passed


def test_dist_matrix_out_of_range_fails(tmp_path):
    p = tmp_path / "d.tsv"
    p.write_text("a\tb\t5.0\t0\t1\n")
    assert not mash_dist_matrix_valid(p).passed


# ---- the observed-taxa-only guard ----

def test_observed_only_passes_with_no_reconstructed():
    assert assess_observed_taxa([]).passed


def test_observed_only_fails_with_reconstructed():
    c = assess_observed_taxa(["Anc_Pan"])
    assert c.status == "FAILED"
    assert "diagnostic" in c.detail.lower()


def test_reconstructed_autodetect_via_warning_file(tmp_path):
    d = tmp_path / "ancestors"
    d.mkdir()
    (d / "NON_DETERMINISTIC_WARNING.txt").write_text("non-deterministic inference")
    (d / "Anc.fa").write_text(">x\nACGT\n")
    assert is_reconstructed(d / "Anc.fa") is True


def test_reconstructed_autodetect_via_provenance(tmp_path):
    import json
    g = tmp_path / "Anc.fa"
    g.write_text(">x\nACGT\n")
    (tmp_path / "Anc.fa.provenance.json").write_text(
        json.dumps({"determinism": {"reproducible": False}}))
    assert is_reconstructed(g) is True


def test_observed_genome_not_flagged(tmp_path):
    g = tmp_path / "homo.fa"
    g.write_text(">chr1\nACGT\n")
    assert is_reconstructed(g) is False


# ---- NJ tree from a known distance matrix ----

def test_nj_tree_recovers_clade(tmp_path):
    # a,b close; c,d close; e outgroup-ish.
    p = tmp_path / "d.tsv"
    pairs = {("a", "b"): 0.01, ("c", "d"): 0.01, ("a", "c"): 0.05, ("a", "d"): 0.05,
             ("b", "c"): 0.05, ("b", "d"): 0.05, ("a", "e"): 0.1, ("b", "e"): 0.1,
             ("c", "e"): 0.1, ("d", "e"): 0.1, ("c", "a"): 0.05}
    taxa = ["a", "b", "c", "d", "e"]
    lines = []
    for x in taxa:
        for y in taxa:
            dv = 0.0 if x == y else pairs.get((x, y), pairs.get((y, x), 0.05))
            lines.append(f"{x}\t{y}\t{dv}\t0\t1")
    p.write_text("\n".join(lines) + "\n")
    nwk = nj_tree_from_mash(p)
    # a and b should be sisters (appear adjacent in the Newick clustering)
    assert "a" in nwk and "b" in nwk and nwk.endswith(";")


# ---- real end-to-end (skips if mash absent) ----

@pytest.mark.skipif(not shutil.which("mash"), reason="mash not installed")
def test_genome_phylo_end_to_end_flags_reconstructed(runner_factory, tmp_path):
    from harness.genome_phylo import run_genome_phylogeny
    runner, _, _ = runner_factory()
    # 4 tiny "genomes"; one declared reconstructed via a warning file.
    def write(label, seq, recon=False):
        d = tmp_path / label
        d.mkdir()
        (d / f"{label}.fa").write_text(f">{label}\n{seq}\n")
        if recon:
            (d / "NON_DETERMINISTIC_WARNING.txt").write_text("non-deterministic")
        return str(d / f"{label}.fa")
    base = "ACGTACGTACGTACGTACGTACGTACGTACGT" * 4
    genomes = {
        "g1": write("g1", base),
        "g2": write("g2", base[:-4] + "TTTT"),
        "g3": write("g3", base[:64] + "GGGG" + base[68:]),
        "anc": write("anc", base, recon=True),   # reconstructed -> must gate the tree
    }
    out = run_genome_phylogeny(runner, run_id="gp", genomes=genomes, workdir=tmp_path / "work")
    assert "anc" in out["reconstructed"]
    # A tree including a reconstructed genome is NOT biologically interpretable.
    assert out["scientific_state"] != "BIOLOGICALLY_INTERPRETABLE"
