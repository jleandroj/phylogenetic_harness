from harness.validators import (
    ValidatorRegistry,
    fasta_valid,
    file_exists,
    file_nonempty,
    newick_valid,
    vcf_header_valid,
)


def test_fasta_valid_passes(tiny_fasta):
    r = fasta_valid(tiny_fasta)
    assert r.passed
    assert r.data["names"] == ["seqA", "seqB"]
    assert r.data["lengths"]["seqA"] == 8


def test_fasta_duplicate_names_fail(bad_fasta_dupes):
    r = fasta_valid(bad_fasta_dupes)
    assert not r.passed
    assert "duplicate" in r.detail


def test_fasta_invalid_residues_fail(bad_fasta_residues):
    r = fasta_valid(bad_fasta_residues)
    assert not r.passed
    assert "invalid residues" in r.detail


def test_newick_valid_passes(tiny_newick):
    r = newick_valid(tiny_newick)
    assert r.passed
    assert "homo_sapiens" in r.data["taxa"]


def test_newick_expected_taxa_missing_fails(tiny_newick):
    r = newick_valid(tiny_newick, expected_taxa=["homo_sapiens", "gorilla_gorilla"])
    assert not r.passed
    assert "gorilla_gorilla" in r.detail


def test_newick_unbalanced_fails(tmp_path):
    p = tmp_path / "bad.nwk"
    p.write_text("((a,b);")
    assert not newick_valid(p).passed


def test_vcf_header_valid(tiny_vcf):
    assert vcf_header_valid(tiny_vcf).passed


def test_vcf_missing_header_fails(tmp_path):
    p = tmp_path / "x.vcf"
    p.write_text("chr1\t1\t.\tA\tT\n")
    assert not vcf_header_valid(p).passed


def test_file_checks(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("data")
    assert file_exists(p).passed
    assert file_nonempty(p).passed
    empty = tmp_path / "e.txt"
    empty.write_text("")
    assert not file_nonempty(empty).passed


def test_registry_run_many(tiny_fasta):
    reg = ValidatorRegistry()
    results = reg.run_many(["file_exists", "file_nonempty", "fasta_valid"], tiny_fasta)
    assert all(r.passed for r in results)
