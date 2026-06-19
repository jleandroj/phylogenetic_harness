"""Audit P0.1/P0.5: the tool gate is ENFORCED on the real execution path."""
import pytest

from harness.tasks import FailurePolicy, ResourceRequest, Task
from harness.tools import ToolUnavailableError, UnregisteredToolError


def make_task(tmp_path, tool_id):
    out = tmp_path / "should_not_exist.txt"
    return Task(
        task_id="r.task_1", run_id="r", task_type="t", tool_id=tool_id,
        command_template="cp a b", command_argv=["cp", str(tmp_path / "a"), str(out)],
        inputs=["a"], outputs_expected=[str(out)], validators=["file_exists"],
        resources=ResourceRequest(memory_gb=1), failure_policy=FailurePolicy(timeout_seconds=30),
    ), out


def test_unregistered_tool_rejected_before_execution(runner_factory, tmp_path):
    runner, events, _ = runner_factory()
    task, out = make_task(tmp_path, "definitely_not_registered")
    with pytest.raises(UnregisteredToolError):
        runner.run_task(task)
    assert not out.exists()  # nothing ran
    # The gate fired before any task_started event.
    names = [e["event"] for e in events.read()]
    assert "task_started" not in names


def test_registered_but_unavailable_tool_rejected(runner_factory, tmp_path):
    runner, events, tools = runner_factory()
    # iqtree2 is registered (tools/iqtree2.yaml) but absent on this host.
    assert tools.get("iqtree2").available is False
    task, out = make_task(tmp_path, "iqtree2")
    with pytest.raises(ToolUnavailableError):
        runner.run_task(task)
    assert not out.exists()
    assert "task_started" not in [e["event"] for e in events.read()]
