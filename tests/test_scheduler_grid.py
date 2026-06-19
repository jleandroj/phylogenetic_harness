"""Audit P1.7 / Q2: a failing cell in the grid is recorded and the rest continue;
resume finishes only what was left."""
from harness.scheduler import Scheduler
from harness.tasks import FailurePolicy, ResourceRequest, Task
from harness.taskstore import is_done


def _copy_task(tmp_path, i, *, good=True):
    src = tmp_path / f"src{i}.txt"
    src.write_text(f"content-{i}\n", encoding="utf-8")
    out = tmp_path / f"out{i}.txt"
    # A "bad" cell declares an output the command will not create -> validator fails.
    declared = out if good else (tmp_path / f"never{i}.txt")
    return Task(
        task_id=f"r.cell{i}", run_id="r", task_type="copy", tool_id="cp",
        command_template="cp", command_argv=["cp", str(src), str(out)],
        inputs=[str(src)], outputs_expected=[str(declared)],
        validators=["file_exists", "file_nonempty"], resources=ResourceRequest(memory_gb=1),
        failure_policy=FailurePolicy(retryable=False, max_retries=0, timeout_seconds=30),
    )


def test_one_cell_fails_rest_continue(runner_factory, tmp_path):
    runner, events, _ = runner_factory()
    rd = runner.results_dir.parent
    tasks = [_copy_task(tmp_path, i, good=(i != 2)) for i in range(5)]  # cell 2 fails
    sched = Scheduler(runner, rd)
    summary = sched.run(tasks)

    assert summary["succeeded"] == 4
    assert summary["failed"] == 1
    assert summary["by_task"]["r.cell2"].startswith("FAILED")
    # The failure was recorded as an event, not swallowed.
    failed_events = [e for e in events.read() if e["event"] == "task_failed" and e.get("task_id") == "r.cell2"]
    assert failed_events
    # The other four really completed.
    for i in (0, 1, 3, 4):
        assert is_done(rd, f"r.cell{i}")


def test_resume_skips_done_and_runs_remainder(runner_factory, tmp_path):
    runner, _, _ = runner_factory()
    rd = runner.results_dir.parent
    tasks = [_copy_task(tmp_path, i) for i in range(3)]
    sched = Scheduler(runner, rd)

    # First pass: run only the first task by hand to simulate a partial run.
    runner.run_task(tasks[0])
    assert is_done(rd, "r.cell0")

    # Resume the full plan: cell0 must be SKIPPED (not re-run), the rest run.
    summary = sched.run(tasks, resume=True)
    assert "r.cell0" in summary["skipped"]
    assert is_done(rd, "r.cell1") and is_done(rd, "r.cell2")
    assert summary["by_task"]["r.cell0"] == "SUCCEEDED"
