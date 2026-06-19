import pytest

from harness.datasets import (
    DatasetManifest,
    ManifestError,
    MissingManifestError,
)


def test_missing_manifest_blocks():
    with pytest.raises(MissingManifestError):
        DatasetManifest.load("/no/such/manifest.yaml")


def test_example_manifest_loads_and_checksums(tmp_path):
    import pathlib
    example = pathlib.Path(__file__).resolve().parent.parent / "schemas" / "dataset.manifest.example.yaml"
    m = DatasetManifest.load(example)
    assert m.dataset_id == "primates_test_v1"
    assert "homo_sapiens" in m.taxa_include
    # excluded taxa carry a reason
    assert m.taxa_exclude[0]["reason"]


def test_low_quality_inputs_flagged(tmp_path):
    import pathlib
    example = pathlib.Path(__file__).resolve().parent.parent / "schemas" / "dataset.manifest.example.yaml"
    m = DatasetManifest.load(example)
    low = {i.sample_id for i in m.low_quality_inputs()}
    assert "pan_paniscus" in low  # marked quality_status: limited


def test_invalid_quality_status_rejected():
    with pytest.raises(ManifestError):
        DatasetManifest.from_dict({
            "dataset_id": "d",
            "dataset_type": "t",
            "scientific_question": "q",
            "inputs": [{"sample_id": "s", "path": "x.fa", "format": "FASTA",
                        "quality_status": "perfect"}],
        })


def test_checksum_computed_for_real_file(tmp_path):
    fa = tmp_path / "g.fa"
    fa.write_text(">a\nACGT\n")
    m = DatasetManifest.from_dict({
        "dataset_id": "d", "dataset_type": "multi_genome", "scientific_question": "q",
        "inputs": [{"sample_id": "a", "path": "g.fa", "format": "FASTA"}],
    }, base_dir=tmp_path)
    m.compute_checksums()
    assert m.inputs[0].checksum.startswith("sha256:")
    assert m.inputs[0].size_bytes == fa.stat().st_size


def test_missing_required_field_rejected():
    with pytest.raises(ManifestError):
        DatasetManifest.from_dict({"dataset_type": "t", "scientific_question": "q"})
