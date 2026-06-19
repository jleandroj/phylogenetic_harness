"""Audit P0.5: real end-to-end run through TaskRunner with a real tool (cp)."""
from harness.states import ScientificState, TechnicalState
from harness.tasks import FailurePolicy, ResourceRequest, Task


def _task(tmp_path, out_path, *, validators=("file_exists", "file_nonempty")):
    src = tmp_path / "src.txt"
    src.write_text("real-content\n", encoding="utf-8")
    return Task(
        task_id="r.task_1", run_id="r", task_type="copy", tool_id="cp",
        command_template="cp src out", command_argv=["cp", str(src), str(out_path)],
        inputs=[str(src)], outputs_expected=[str(out_path)], validators=list(validators),
        resources=ResourceRequest(memory_gb=1), failure_policy=FailurePolicy(timeout_seconds=30),
    )


def test_e2e_success_reaches_succeeded(runner_factory, tmp_path):
    runner, events, _ = runner_factory()
    out = tmp_path / "copied.txt"
    task = _task(tmp_path, out)
    bundle = runner.run_task(task)
    assert out.exists()
    assert task.status_technical == TechnicalState.SUCCEEDED
    assert bundle["status_technical"] == "SUCCEEDED"
    # Core invariant: technical success does NOT auto-promote to a biological claim.
    assert task.status_scientific != ScientificState.BIOLOGICALLY_INTERPRETABLE
    # Full lifecycle recorded.
    names = [e["event"] for e in events.read()]
    for required in ("task_approved", "task_leased", "task_started", "command_finished",
                     "validation_succeeded", "task_succeeded"):
        assert required in names


def test_validator_failure_forces_failed_not_succeeded(runner_factory, tmp_path):
    """If the expected output is never produced, a validator fails and the task
    must NOT end SUCCEEDED — proving validation gates the terminal state."""
    runner, events, _ = runner_factory()
    out = tmp_path / "copied.txt"
    task = _task(tmp_path, out)
    # Declare an output the command will NOT create.
    task.outputs_expected = [str(tmp_path / "never_created.txt")]
    bundle = runner.run_task(task)
    assert task.status_technical in (TechnicalState.FAILED_RETRYABLE, TechnicalState.FAILED_FATAL)
    assert bundle["status_technical"] != "SUCCEEDED"
    assert "validation_failed" in [e["event"] for e in events.read()]


def test_results_bundle_written(runner_factory, tmp_path):
    runner, _, _ = runner_factory()
    out = tmp_path / "copied.txt"
    runner.run_task(_task(tmp_path, out))
    bundle_file = runner.results_dir / "r.task_1.validation.json"
    assert bundle_file.exists()
