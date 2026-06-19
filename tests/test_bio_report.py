"""The per-run §24.14 report has all 13 mandatory sections, sourced from disk."""
import json

from harness.bio_report import generate_pipeline_report
from harness.report import MANDATORY_SECTIONS


def _fake_run(tmp_path):
    rd = tmp_path / "run"
    (rd / "results").mkdir(parents=True)
    (rd / "RUN_CONFIG.json").write_text(json.dumps({"run_id": "r", "config_hash": "h", "seed": 42}))
    (rd / "TOOLS.lock.json").write_text(json.dumps({"iqtree": {"version": "IQ-TREE 3", "available": True}}))
    bundle = {
        "task_id": "r.iqtree_geneA", "task_type": "tree_iqtree_mfp", "tool_id": "iqtree",
        "status_technical": "SUCCEEDED", "status_scientific": "BIOLOGICALLY_INTERPRETABLE",
        "degenerate": False, "validators_passed": True,
        "outputs": [{"path": str(rd / "geneA.treefile"), "sha256": "sha256:abc"}],
        "execution": {"resources": {"wall_seconds": 0.8, "max_rss_mb": 16.5,
                                    "rss_source": "per_pid", "status": "RESOURCE_AUDIT_OK"}},
        "interpretation": {"interpretation_not_allowed": ["does not prove a single true tree"],
                          "validation": {"statistical": {"checks": [
                              {"name": "model_selected", "status": "PASSED", "data": {"model": "TPM3+I"}}]}}},
    }
    (rd / "results" / "r.iqtree_geneA.validation.json").write_text(json.dumps(bundle))
    (rd / "geneA.treefile").write_text("(taxon_0:0.1,taxon_1:0.1,(taxon_2:0.1,taxon_3:0.1)90:0.2);")
    return rd


def test_report_has_all_13_sections(tmp_path):
    rd = _fake_run(tmp_path)
    paths = generate_pipeline_report(rd)
    md = open(paths["markdown"]).read()
    for heading in MANDATORY_SECTIONS:
        assert f"## {heading}" in md, heading
    # The selected model and the standing prohibition are surfaced.
    assert "TPM3+I" in md
    assert "true" in md.lower()


def test_report_json_roundtrips(tmp_path):
    rd = _fake_run(tmp_path)
    paths = generate_pipeline_report(rd)
    data = json.loads(open(paths["json"]).read())
    assert data["run_id"] == "r"
    assert data["config_hash"] == "h"
