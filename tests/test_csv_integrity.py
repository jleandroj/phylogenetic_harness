"""Audit Q4: the results CSV cannot report a degenerate/unvalidated result as good."""
import json

from harness.aggregate import aggregate_run, read_rows
from harness.tasks import FailurePolicy, ResourceRequest, Task


def _run_dir(runner):
    return runner.results_dir.parent


def _seed_run_config(run_dir):
    (run_dir / "RUN_CONFIG.json").write_text(
        json.dumps({"run_id": "r", "config_hash": "deadbeef", "seed": 42}), encoding="utf-8"
    )
    (run_dir / "TOOLS.lock.json").write_text(
        json.dumps({"cp": {"version": "cp (GNU coreutils) 9.x", "available": True}}), encoding="utf-8"
    )


def test_degenerate_output_is_marked_not_clean(runner_factory, tmp_path):
    runner, _, _ = runner_factory()
    rd = _run_dir(runner)
    _seed_run_config(rd)

    # Empty source -> empty output. Only file_exists is required, so the task
    # SUCCEEDS technically, but the output is degenerate (empty).
    src = tmp_path / "empty.src"
    src.write_text("", encoding="utf-8")
    out = tmp_path / "empty.out"
    task = Task(
        task_id="r.degen", run_id="r", task_type="copy", tool_id="cp",
        command_template="cp", command_argv=["cp", str(src), str(out)],
        inputs=[str(src)], outputs_expected=[str(out)], validators=["file_exists"],
        resources=ResourceRequest(memory_gb=1),
    )
    bundle = runner.run_task(task)
    assert bundle["status_technical"] == "SUCCEEDED"      # technically ran fine
    assert bundle["degenerate"] is True                   # but flagged degenerate

    aggregate_run(rd)
    rows = {r["task_id"]: r for r in read_rows(rd)}
    row = rows["r.degen"]
    assert row["degenerate"] == "True"
    assert row["scientific_state"] == "DEGENERATE"
    assert row["trustworthy"] == "False"                  # never reported as good


def test_validator_failure_not_reported_as_success(runner_factory, tmp_path):
    runner, _, _ = runner_factory()
    rd = _run_dir(runner)
    _seed_run_config(rd)

    src = tmp_path / "s.txt"
    src.write_text("data\n", encoding="utf-8")
    task = Task(
        task_id="r.fail", run_id="r", task_type="copy", tool_id="cp",
        command_template="cp", command_argv=["cp", str(src), str(tmp_path / 'real.out')],
        inputs=[str(src)], outputs_expected=[str(tmp_path / "never.out")],  # not created
        validators=["file_exists"], resources=ResourceRequest(memory_gb=1),
        failure_policy=FailurePolicy(retryable=False, max_retries=0),
    )
    runner.run_task(task)
    aggregate_run(rd)
    row = {r["task_id"]: r for r in read_rows(rd)}["r.fail"]
    assert row["technical_state"] != "SUCCEEDED"
    assert row["trustworthy"] == "False"


def test_clean_result_is_trustworthy(runner_factory, tmp_path):
    runner, _, _ = runner_factory()
    rd = _run_dir(runner)
    _seed_run_config(rd)
    src = tmp_path / "s.txt"
    src.write_text("real-content\n", encoding="utf-8")
    out = tmp_path / "ok.out"
    task = Task(
        task_id="r.ok", run_id="r", task_type="copy", tool_id="cp",
        command_template="cp", command_argv=["cp", str(src), str(out)],
        inputs=[str(src)], outputs_expected=[str(out)],
        validators=["file_exists", "file_nonempty"], resources=ResourceRequest(memory_gb=1),
    )
    runner.run_task(task)
    aggregate_run(rd)
    row = {r["task_id"]: r for r in read_rows(rd)}["r.ok"]
    assert row["trustworthy"] == "True"
    assert row["output_sha256"].startswith("sha256:")
    assert row["tool_version"]  # provenance present from TOOLS.lock


def test_row_roundtrips_from_bundle(runner_factory, tmp_path):
    runner, _, _ = runner_factory()
    rd = _run_dir(runner)
    _seed_run_config(rd)
    src = tmp_path / "s.txt"
    src.write_text("x\n", encoding="utf-8")
    out = tmp_path / "o.txt"
    task = Task(
        task_id="r.rt", run_id="r", task_type="copy", tool_id="cp",
        command_template="cp", command_argv=["cp", str(src), str(out)],
        inputs=[str(src)], outputs_expected=[str(out)], validators=["file_exists"],
        resources=ResourceRequest(memory_gb=1),
    )
    runner.run_task(task)
    aggregate_run(rd)
    row = {r["task_id"]: r for r in read_rows(rd)}["r.rt"]
    bundle = json.loads((rd / "results" / "r.rt.validation.json").read_text())
    assert row["technical_state"] == bundle["status_technical"]
    assert row["scientific_state"] == bundle["status_scientific"]
