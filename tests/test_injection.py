"""Audit P0.2/P0.5: hostile params are inert — no shell, no execution of metachars."""
from harness.tasks import FailurePolicy, ResourceRequest, Task


def test_render_argv_keeps_malicious_value_as_single_token():
    t = Task(
        task_id="t", run_id="r", task_type="x", tool_id="cp",
        command_template="echo {x}", inputs=["i"], outputs_expected=["o"],
        validators=["file_exists"], params={"x": "; rm -rf ~"},
    )
    argv = t.render_argv()
    assert argv == ["echo", "; rm -rf ~"]  # one literal element, not parsed


def test_real_cp_with_malicious_dest_does_not_run_shell(runner_factory, tmp_path):
    """A destination param containing shell metacharacters must create a file with
    that LITERAL name and must NOT delete the canary."""
    runner, _, _ = runner_factory()
    canary = tmp_path / "canary"
    canary.write_text("alive\n", encoding="utf-8")
    src = tmp_path / "src.txt"
    src.write_text("data\n", encoding="utf-8")
    # Metacharacters but no path separators, so the literal name is a valid filename.
    evil_name = "; rm -rf canary $(whoami)"
    dst = tmp_path / evil_name
    task = Task(
        task_id="r.t1", run_id="r", task_type="copy", tool_id="cp",
        command_template="cp src dst", command_argv=["cp", str(src), str(dst)],
        inputs=[str(src)], outputs_expected=[str(dst)], validators=["file_exists"],
        resources=ResourceRequest(memory_gb=1), failure_policy=FailurePolicy(timeout_seconds=30),
    )
    runner.run_task(task)
    assert canary.exists()              # the shell never ran `rm`
    assert dst.exists()                 # cp created a file literally named with the metachars


def test_string_command_cannot_reach_executor(runner_factory, tmp_path):
    """Even if someone bypasses render_argv, the executor rejects string commands."""
    import pytest

    from harness.executor import LocalExecutor, ShellCommandRejected
    ex = LocalExecutor(tmp_path, disk_path=tmp_path)
    with pytest.raises(ShellCommandRejected):
        ex.run("t", "rm -rf /")
