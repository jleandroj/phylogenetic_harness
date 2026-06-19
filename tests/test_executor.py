import sys

import pytest

from harness.clock import counting_clock
from harness.executor import (
    AuditOnlyExecutor,
    DryRunExecutor,
    LocalExecutor,
    ShellCommandRejected,
    get_executor,
)

PY = sys.executable


def test_string_command_rejected(run_dir):
    ex = LocalExecutor(run_dir, clock_fn=counting_clock(), disk_path=run_dir)
    with pytest.raises(ShellCommandRejected):
        ex.run("t0", "echo hello")  # strings (shell) are forbidden


def test_captures_stdout_to_file(run_dir):
    ex = LocalExecutor(run_dir, clock_fn=counting_clock(), disk_path=run_dir)
    res = ex.run("t1", ["echo", "hello-harness"])
    assert res.exit_code == 0
    assert res.succeeded
    assert "hello-harness" in open(res.stdout_path).read()


def test_captures_stderr_and_nonzero_exit(run_dir):
    ex = LocalExecutor(run_dir, clock_fn=counting_clock(), disk_path=run_dir)
    res = ex.run("t2", [PY, "-c", "import sys; sys.stderr.write('oops'); sys.exit(3)"])
    assert res.exit_code == 3
    assert not res.succeeded
    assert "oops" in open(res.stderr_path).read()


def test_timeout_marks_timed_out(run_dir):
    ex = LocalExecutor(run_dir, clock_fn=counting_clock(), disk_path=run_dir)
    res = ex.run("t3", ["sleep", "5"], timeout_seconds=1)
    assert res.timed_out is True
    assert not res.succeeded


def test_resources_measured(run_dir):
    ex = LocalExecutor(run_dir, clock_fn=counting_clock(), disk_path=run_dir)
    res = ex.run("t4", ["echo", "x"])
    assert res.resources is not None
    assert res.resources.wall_seconds is not None


def test_gpu_pinning_sets_visible_devices(run_dir):
    ex = LocalExecutor(run_dir, clock_fn=counting_clock(), disk_path=run_dir)
    res = ex.run("t5", ["printenv", "CUDA_VISIBLE_DEVICES"], gpu_assigned="2")
    assert res.gpu_assigned == "2"
    assert open(res.stdout_path).read().strip() == "2"


def test_dry_run_does_not_execute(run_dir):
    ex = DryRunExecutor(run_dir, clock_fn=counting_clock())
    res = ex.run("t6", ["echo", "should-not-run"])
    assert res.exit_code is None
    assert "dry_run" in res.error


def test_audit_only_disabled(run_dir):
    ex = AuditOnlyExecutor(run_dir, clock_fn=counting_clock())
    res = ex.run("t7", ["echo", "nope"])
    assert "audit_only" in res.error


def test_attempt_in_log_name(run_dir):
    ex = LocalExecutor(run_dir, clock_fn=counting_clock(), disk_path=run_dir)
    res = ex.run("t8", ["echo", "x"], attempt=2)
    assert "attempt2" in res.stdout_path


def test_get_executor_factory(run_dir):
    assert get_executor("local", run_dir).name == "local"
    assert get_executor("dry_run", run_dir).name == "dry_run"
