"""P0.2: a real flaky tool that fails once then succeeds is RETRIED by the runner
and ends SUCCEEDED — proving max_retries covers execution failures, not just
lease expiry. The flakiness is real (a subprocess exits 1 the first time)."""
import sys

from harness.tasks import FailurePolicy, ResourceRequest, Task
from harness.tools import ToolContract

FLAKY = """
import sys, os
sentinel, output = sys.argv[1], sys.argv[2]
if not os.path.exists(sentinel):
    open(sentinel, 'w').close()
    sys.exit(1)          # first attempt fails
with open(output, 'w') as fh:
    fh.write('ok')       # subsequent attempts succeed
"""


def _register_python_tool(runner):
    runner.tools.register(ToolContract(
        tool_id="pyflaky", tool_name="python (flaky test tool)",
        version_command=[sys.executable, "--version"],
    ))


def test_flaky_tool_is_retried_and_succeeds(runner_factory, tmp_path):
    runner, events, _ = runner_factory()
    _register_python_tool(runner)
    script = tmp_path / "flaky.py"
    script.write_text(FLAKY, encoding="utf-8")
    sentinel = tmp_path / "sentinel"
    output = tmp_path / "out.txt"

    task = Task(
        task_id="r.flaky", run_id="r", task_type="flaky", tool_id="pyflaky",
        command_template="py flaky", command_argv=[sys.executable, str(script), str(sentinel), str(output)],
        inputs=[str(script)], outputs_expected=[str(output)],
        validators=["file_exists", "file_nonempty"],
        resources=ResourceRequest(memory_gb=1),
        failure_policy=FailurePolicy(retryable=True, max_retries=2, timeout_seconds=30),
    )
    bundle = runner.run_task(task)

    assert bundle["status_technical"] == "SUCCEEDED"
    assert task.retries == 1                      # failed once, retried once
    assert output.exists()
    names = [e["event"] for e in events.read()]
    assert "task_requeued" in names               # a retry actually happened
    # Per-attempt logs both exist (no clobbering).
    logs = sorted(p.name for p in (runner.executor.log_dir).glob("r.flaky.attempt*.stdout.log"))
    assert "r.flaky.attempt1.stdout.log" in logs
    assert "r.flaky.attempt2.stdout.log" in logs


def test_always_failing_tool_exhausts_retries_to_fatal(runner_factory, tmp_path):
    runner, events, _ = runner_factory()
    runner.tools.register(ToolContract(
        tool_id="pyfail", tool_name="always-fail", version_command=[sys.executable, "--version"],
    ))
    script = tmp_path / "fail.py"
    script.write_text("import sys; sys.exit(7)\n", encoding="utf-8")
    out = tmp_path / "never.txt"
    task = Task(
        task_id="r.fail", run_id="r", task_type="fail", tool_id="pyfail",
        command_template="py fail", command_argv=[sys.executable, str(script)],
        inputs=[str(script)], outputs_expected=[str(out)], validators=["file_exists"],
        resources=ResourceRequest(memory_gb=1),
        failure_policy=FailurePolicy(retryable=True, max_retries=2, timeout_seconds=30),
    )
    bundle = runner.run_task(task)
    assert bundle["status_technical"] == "FAILED_FATAL"
    assert task.retries == 2                       # exhausted the budget
    assert not runner.leases._leases               # no zombie lease
