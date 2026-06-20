"""A pipeline run is resumable: re-running skips completed tasks (does not
re-execute them) and finishes the rest. Proven with a counter tool."""
import sys

from harness import taskstore
from harness.tasks import FailurePolicy, ResourceRequest, Task
from harness.tools import ToolContract

# A tool that appends a line to a counter file each time it runs, then writes the
# declared output. If a task is skipped on resume, the counter does NOT grow.
COUNTER = """
import sys
counter, output = sys.argv[1], sys.argv[2]
with open(counter, 'a') as fh:
    fh.write('run\\n')
with open(output, 'w') as fh:
    fh.write('done')
"""


def _task(tmp_path, tid, out_name):
    script = tmp_path / "counter.py"
    script.write_text(COUNTER, encoding="utf-8")
    counter = tmp_path / "counter.txt"
    out = tmp_path / out_name
    return Task(
        task_id=tid, run_id="r", task_type="count", tool_id="pytool",
        command_template="py", command_argv=[sys.executable, str(script), str(counter), str(out)],
        inputs=[str(script)], outputs_expected=[str(out)],
        validators=["file_exists", "file_nonempty"], resources=ResourceRequest(memory_gb=1),
        failure_policy=FailurePolicy(retryable=False, max_retries=0, timeout_seconds=30),
    ), counter, out


def test_run_or_resume_skips_completed_task(runner_factory, tmp_path):
    runner, _, _ = runner_factory()
    runner.tools.register(ToolContract(
        tool_id="pytool", tool_name="python", version_command=[sys.executable, "--version"]))
    rd = runner.results_dir.parent

    task, counter, out = _task(tmp_path, "r.t1", "out1.txt")

    # First execution: runs once.
    b1 = taskstore.run_or_resume(runner, task)
    assert b1["status_technical"] == "SUCCEEDED"
    assert counter.read_text().count("run") == 1
    # Plan was persisted for audit/recovery.
    assert "r.t1" in {t.task_id for t in taskstore.load_tasks(rd)}

    # Re-run (resume): SUCCEEDED bundle + output present -> SKIPPED, counter unchanged.
    b2 = taskstore.run_or_resume(runner, task)
    assert b2["status_technical"] == "SUCCEEDED"
    assert counter.read_text().count("run") == 1   # did NOT re-execute


def test_resume_reruns_when_output_missing(runner_factory, tmp_path):
    runner, _, _ = runner_factory()
    runner.tools.register(ToolContract(
        tool_id="pytool", tool_name="python", version_command=[sys.executable, "--version"]))
    task, counter, out = _task(tmp_path, "r.t2", "out2.txt")

    taskstore.run_or_resume(runner, task)
    assert counter.read_text().count("run") == 1
    out.unlink()                                   # output lost (simulate partial crash)
    taskstore.run_or_resume(runner, task)          # must re-run to reproduce it
    assert counter.read_text().count("run") == 2
    assert out.exists()


def test_plan_persisted_and_skip_idempotent(runner_factory, tmp_path):
    runner, _, _ = runner_factory()
    runner.tools.register(ToolContract(
        tool_id="pytool", tool_name="python", version_command=[sys.executable, "--version"]))
    rd = runner.results_dir.parent
    task, _, _ = _task(tmp_path, "r.t3", "out3.txt")
    for _ in range(3):
        taskstore.run_or_resume(runner, task)
    # The plan records the task exactly once despite repeated runs.
    plan = [t.task_id for t in taskstore.load_tasks(rd)]
    assert plan.count("r.t3") == 1
