"""Audit-4 #1: lifecycle hooks fire at pre/post/error, are auditable via hook_*
events, and are ERROR-ISOLATED (a failing hook never breaks the run)."""
import sys

from harness.events import EventStore
from harness.hooks import HookRegistry
from harness.tasks import FailurePolicy, ResourceRequest, Task
from harness.tools import ToolContract


def _ok_task(tmp_path):
    src = tmp_path / "s.txt"
    src.write_text("x\n")
    out = tmp_path / "o.txt"
    return Task(
        task_id="r.ok", run_id="r", task_type="copy", tool_id="cp",
        command_template="cp", command_argv=["cp", str(src), str(out)],
        inputs=[str(src)], outputs_expected=[str(out)], validators=["file_exists"],
        resources=ResourceRequest(memory_gb=1),
    )


# ---- isolated unit tests of the registry ----

def test_pre_post_fire_and_are_audited(tmp_path):
    events = EventStore(tmp_path / "e.jsonl")
    reg = HookRegistry(events=events)
    calls = []
    reg.on_pre_task(lambda task: calls.append(("pre", task.task_id)))
    reg.on_post_task(lambda task, bundle: calls.append(("post", bundle["status_technical"])))

    class T:
        task_id = "t1"
    reg.fire_pre(T())
    reg.fire_post(T(), {"status_technical": "SUCCEEDED"})
    assert calls == [("pre", "t1"), ("post", "SUCCEEDED")]
    names = [e["event"] for e in events.read()]
    assert names.count("hook_fired") == 2 and names.count("hook_succeeded") == 2


def test_failing_hook_is_isolated(tmp_path):
    events = EventStore(tmp_path / "e.jsonl")
    reg = HookRegistry(events=events)
    reg.on_pre_task(lambda task: (_ for _ in ()).throw(RuntimeError("boom")))
    ran_after = []
    reg.on_pre_task(lambda task: ran_after.append(1))

    class T:
        task_id = "t1"
    reg.fire_pre(T())  # must NOT raise
    assert ran_after == [1]                       # later hook still ran
    names = [e["event"] for e in events.read()]
    assert "hook_failed" in names                 # the failure was recorded


# ---- integration: the runner fires hooks across the lifecycle ----

def test_runner_fires_lifecycle_hooks(runner_factory, tmp_path):
    runner, events, _ = runner_factory()
    seen = []
    runner.hooks.on_pre_task(lambda task: seen.append("pre"))
    runner.hooks.on_post_task(lambda task, bundle: seen.append("post"))
    runner.run_task(_ok_task(tmp_path))
    assert seen == ["pre", "post"]
    assert any(e["event"] == "hook_succeeded" for e in events.read())


def test_runner_fires_error_hook_on_failure(runner_factory, tmp_path):
    runner, events, _ = runner_factory()
    runner.validators.register("boom", lambda _p, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    fired = []
    runner.hooks.on_error(lambda task, exc: fired.append(type(exc).__name__))
    runner.tools.register(ToolContract(tool_id="pytool", tool_name="py",
                                       version_command=[sys.executable, "--version"]))
    src = tmp_path / "s.txt"
    src.write_text("x")
    out = tmp_path / "o.txt"
    task = Task(
        task_id="r.boom", run_id="r", task_type="x", tool_id="cp",
        command_template="cp", command_argv=["cp", str(src), str(out)],
        inputs=[str(src)], outputs_expected=[str(out)], validators=["boom"],
        resources=ResourceRequest(memory_gb=1),
        failure_policy=FailurePolicy(retryable=False, max_retries=0),
    )
    runner.run_task(task)
    assert fired == ["RuntimeError"]
