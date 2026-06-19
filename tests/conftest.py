import sys
from pathlib import Path

import pytest

# Make the package importable when running pytest from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def run_dir(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    return d


@pytest.fixture
def tiny_fasta(tmp_path):
    p = tmp_path / "tiny.fa"
    p.write_text(">seqA\nACGTACGT\n>seqB\nACGTAAAA\n", encoding="utf-8")
    return p


@pytest.fixture
def bad_fasta_dupes(tmp_path):
    p = tmp_path / "dupes.fa"
    p.write_text(">seqA\nACGT\n>seqA\nTTTT\n", encoding="utf-8")
    return p


@pytest.fixture
def bad_fasta_residues(tmp_path):
    p = tmp_path / "bad.fa"
    p.write_text(">seqA\nACGTXZ123\n", encoding="utf-8")
    return p


@pytest.fixture
def tiny_newick(tmp_path):
    p = tmp_path / "tree.nwk"
    p.write_text("((homo_sapiens:0.1,pan_troglodytes:0.1):0.2,pongo_abelii:0.3);", encoding="utf-8")
    return p


@pytest.fixture
def tiny_vcf(tmp_path):
    p = tmp_path / "tiny.vcf"
    p.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t100\t.\tA\tT\t50\tPASS\t.\n",
        encoding="utf-8",
    )
    return p
