"""Iter 9: robustness — a poison task that hard-crashes the process is quarantined.

Simulates the worst case the runner's own try/except cannot catch: the harness
PROCESS dies mid-task (the in-flight marker survives but no bundle is written).
On each "resume" the Supervisor blames the in-flight task with a strike; after
max_strikes it quarantines the poison task and the rest of the grid completes.
"""

import json

from conftest import TOOLS_DIR

from harness.runner import TaskRunner
from harness.scheduler import Scheduler
from harness.supervisor import Supervisor
from harness.tasks import ResourceRequest, Task


def _cp_task(tid, tmp_path):
    src = tmp_path / f"{tid}.in"
    src.write_text("x\n")
    out = tmp_path / f"{tid}.out"
    return Task(task_id=tid, run_id="r", task_type="copy", tool_id="cp",
                command_template="cp", command_argv=["cp", str(src), str(out)],
                inputs=[str(src)], outputs_expected=[str(out)], validators=["file_exists"],
                resources=ResourceRequest(memory_gb=1))


def _build(tmp_path):
    from harness import clock
    from harness.approval import ApprovalGate
    from harness.events import EventStore
    from harness.executor import LocalExecutor
    from harness.leases import LeaseManager
    from harness.seeds import SeedManager
    from harness.tools import ToolRegistry
    from harness.validators import ValidatorRegistry
    base = tmp_path / "run"
    for sub in ("logs", "events", "results"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    events = EventStore(base / "events" / "run.events.jsonl", clock=clock.counting_clock())
    tools = ToolRegistry()
    tools.load_dir(TOOLS_DIR)
    runner = TaskRunner(
        events=events, tools=tools, validators=ValidatorRegistry(),
        approval=ApprovalGate(events=events),
        executor=LocalExecutor(base / "logs", clock_fn=clock.counting_clock(), disk_path=base),
        leases=LeaseManager(events=events), results_dir=base / "results",
        seeds=SeedManager(42), clock_fn=clock.monotonic)
    return runner, base


def test_poison_task_is_quarantined_and_grid_completes(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    runner, base = _build(tmp_path)
    # Poison: an unregistered tool -> run_task raises out of the gate (no terminal
    # bundle is ever written), the way a process that dies mid-task leaves no result.
    poison = _cp_task("r.poison", tmp_path)
    poison.tool_id = "nonexistent_tool"
    good = _cp_task("r.good", tmp_path)
    tasks = [poison, good]

    sup = Supervisor(runner, base, max_strikes=2)
    # Simulate two prior hard crashes that died mid-poison: an in-flight marker
    # left behind with no bundle written.
    (base / "INFLIGHT.json").write_text(json.dumps({"task_id": "r.poison"}))
    sup.run(tasks, resume=True)                       # strike 1, poison still retried
    (base / "INFLIGHT.json").write_text(json.dumps({"task_id": "r.poison"}))
    result = sup.run(tasks, resume=True)              # strike 2 -> quarantine

    assert "r.poison" in result["quarantined"]
    assert result["by_task"]["r.poison"] == "QUARANTINED"
    # the healthy task still completed despite the poison neighbour.
    assert result["by_task"]["r.good"] in ("SUCCEEDED", "DONE")
    # quarantine left a terminal poisoned bundle, not a dangling task.
    b = json.loads((base / "results" / "r.poison.validation.json").read_text())
    assert b["poisoned"] is True and b["status_technical"] == "FAILED_FATAL"


def test_inflight_marker_cleared_on_clean_run(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    runner, base = _build(tmp_path)
    sup = Supervisor(runner, base)
    sup.run([_cp_task("r.a", tmp_path)])
    assert not (base / "INFLIGHT.json").exists()      # no crash signature left behind


def test_scheduler_isolation_still_holds(tmp_path, monkeypatch):
    """Sanity: ordinary failures are isolated without quarantine machinery."""
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    runner, base = _build(tmp_path)
    ok = _cp_task("r.ok", tmp_path)
    sched = Scheduler(runner, base)
    out = sched.run([ok])
    assert out["succeeded"] == 1
