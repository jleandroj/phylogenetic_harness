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


def _add_astral(rd, state):
    bundle = {
        "task_id": "r.astral", "task_type": "species_tree_astral", "tool_id": "astral",
        "status_technical": "SUCCEEDED", "status_scientific": state,
        "degenerate": False, "validators_passed": True,
        "outputs": [{"path": str(rd / "species.nwk"), "sha256": "sha256:sp"}],
        "execution": {"resources": {}},
        "interpretation": {"interpretation_not_allowed": ["ASTRAL assumes ILS only"],
                          "validation": {"statistical": {"checks": []}}},
    }
    (rd / "results" / "r.astral.validation.json").write_text(json.dumps(bundle))
    (rd / "species.nwk").write_text("(taxon_0,(taxon_1,(taxon_2,taxon_3)));")


def test_report_does_not_hardcode_species_state(tmp_path):
    """Regression: the summary must reflect the ASTRAL bundle's ACTUAL scientific
    state, never a hardcoded 'LOW_CONFIDENCE' (a report must not misreport)."""
    rd = _fake_run(tmp_path)
    _add_astral(rd, "BIOLOGICALLY_INTERPRETABLE")
    md = open(generate_pipeline_report(rd)["markdown"]).read()
    assert "built, BIOLOGICALLY_INTERPRETABLE" in md
    assert "LOW_CONFIDENCE on few loci" not in md
    sec5 = md.split("## 5.")[1].split("## 6.")[0]
    assert "under-powered" not in sec5


def test_report_flags_low_confidence_species_tree(tmp_path):
    rd = _fake_run(tmp_path)
    _add_astral(rd, "LOW_CONFIDENCE")
    md = open(generate_pipeline_report(rd)["markdown"]).read()
    assert "built, LOW_CONFIDENCE" in md
    assert "under-powered" in md.split("## 5.")[1].split("## 6.")[0]
