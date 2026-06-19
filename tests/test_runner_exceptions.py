"""P0.1: an exception anywhere in the attempt leaves a TERMINAL state, emits
task_failed, and never leaks a lease (no zombie)."""
from harness.states import TechnicalState
from harness.tasks import FailurePolicy, ResourceRequest, Task
from harness.validators import CheckResult


def _task(tmp_path):
    out = tmp_path / "out.txt"
    src = tmp_path / "src.txt"
    src.write_text("x\n", encoding="utf-8")
    return Task(
        task_id="r.t1", run_id="r", task_type="copy", tool_id="cp",
        command_template="cp src out", command_argv=["cp", str(src), str(out)],
        inputs=[str(src)], outputs_expected=[str(out)], validators=["boom"],
        resources=ResourceRequest(memory_gb=1),
        failure_policy=FailurePolicy(max_retries=2, timeout_seconds=30),
    )


def test_validator_exception_is_contained(runner_factory, tmp_path):
    runner, events, _ = runner_factory()

    def boom(_path, **_kw):
        raise RuntimeError("validator exploded")

    runner.validators.register("boom", boom)
    bundle = runner.run_task(_task(tmp_path))

    # Terminal, failed, NOT a zombie.
    assert bundle["status_technical"] == "FAILED_FATAL"
    assert runner.leases._leases == {}  # lease released
    names = [e["event"] for e in events.read()]
    assert "task_failed" in names
    # The exception did not leak past the runner.


def test_no_lease_left_after_exception(runner_factory, tmp_path):
    runner, _, _ = runner_factory()

    def boom(_path, **_kw):
        raise ValueError("kaboom")

    runner.validators.register("boom", boom)
    t = _task(tmp_path)
    runner.run_task(t)
    assert t.status_technical == TechnicalState.FAILED_FATAL
    assert not runner.leases._leases


def test_exception_bundle_persisted(runner_factory, tmp_path):
    runner, _, _ = runner_factory()
    runner.validators.register("boom", lambda _p, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    runner.run_task(_task(tmp_path))
    assert (runner.results_dir / "r.t1.validation.json").exists()


def test_normal_validator_still_works(runner_factory, tmp_path):
    """Control: a non-throwing validator path is unaffected."""
    runner, _, _ = runner_factory()
    runner.validators.register("ok", lambda p, **_k: CheckResult("ok", "PASSED"))
    out = tmp_path / "out.txt"
    src = tmp_path / "src.txt"
    src.write_text("x\n")
    t = Task(
        task_id="r.t2", run_id="r", task_type="copy", tool_id="cp",
        command_template="cp", command_argv=["cp", str(src), str(out)],
        inputs=[str(src)], outputs_expected=[str(out)], validators=["ok"],
        resources=ResourceRequest(memory_gb=1),
    )
    bundle = runner.run_task(t)
    assert bundle["status_technical"] == "SUCCEEDED"
