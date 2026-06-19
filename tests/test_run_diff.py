"""Audit Q5: rigorous run-to-run comparison names the drift instead of guessing."""
import json

from harness.diff import diff_runs


def _make_run(tmp_path, name, *, config_hash, seed, tool_version, task_state, sci, out_sha):
    rd = tmp_path / name
    (rd / "results").mkdir(parents=True)
    (rd / "RUN_CONFIG.json").write_text(json.dumps({"run_id": name, "config_hash": config_hash, "seed": seed}))
    (rd / "TOOLS.lock.json").write_text(json.dumps({"cp": {"version": tool_version, "available": True}}))
    (rd / "results" / "t1.validation.json").write_text(json.dumps({
        "task_id": "t1", "status_technical": task_state, "status_scientific": sci,
        "outputs": [{"path": "out.txt", "sha256": out_sha}],
    }))
    return rd


def test_identical_runs_no_drift(tmp_path):
    a = _make_run(tmp_path, "a", config_hash="h1", seed=42, tool_version="cp 9.1",
                  task_state="SUCCEEDED", sci="LOW_CONFIDENCE", out_sha="sha256:abc")
    b = _make_run(tmp_path, "b", config_hash="h1", seed=42, tool_version="cp 9.1",
                  task_state="SUCCEEDED", sci="LOW_CONFIDENCE", out_sha="sha256:abc")
    report = diff_runs(a, b)
    assert report["unchanged"] is True
    assert report["config_drift"] == [] and report["version_drift"] == []
    assert report["seed_drift"] == [] and report["result_drift"] == []


def test_seed_and_version_and_result_drift_flagged(tmp_path):
    a = _make_run(tmp_path, "a", config_hash="h1", seed=42, tool_version="cp 9.1",
                  task_state="SUCCEEDED", sci="LOW_CONFIDENCE", out_sha="sha256:abc")
    b = _make_run(tmp_path, "b", config_hash="h2", seed=7, tool_version="cp 9.5",
                  task_state="FAILED_FATAL", sci="NOT_BIOLOGICALLY_INTERPRETABLE", out_sha="sha256:zzz")
    report = diff_runs(a, b)
    assert report["unchanged"] is False
    assert report["config_drift"]
    assert any("seed" in d for d in report["seed_drift"])
    assert any("cp" in d for d in report["version_drift"])
    assert any("t1" in d for d in report["result_drift"])


def test_output_checksum_drift_alone(tmp_path):
    a = _make_run(tmp_path, "a", config_hash="h1", seed=42, tool_version="cp 9.1",
                  task_state="SUCCEEDED", sci="LOW_CONFIDENCE", out_sha="sha256:abc")
    b = _make_run(tmp_path, "b", config_hash="h1", seed=42, tool_version="cp 9.1",
                  task_state="SUCCEEDED", sci="LOW_CONFIDENCE", out_sha="sha256:DIFFERENT")
    report = diff_runs(a, b)
    assert report["unchanged"] is False
    assert any("checksum" in d for d in report["result_drift"])
