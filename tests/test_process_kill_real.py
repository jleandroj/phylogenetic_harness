"""Audit P0.3/P0.5: a REAL SIGKILL mid-RUNNING leaves no zombie.

We start a real child process, register the task as LEASED+RUNNING in the event
log, kill the child with SIGKILL, then prove that (a) rebuild_state from the log
detects the task as an orphan, and (b) the reaper requeues it so it is no longer
a zombie. The death is real (a real PID is killed) — no injected clock substitutes
for it.
"""
import os
import signal
import subprocess
import sys
import time

from harness.events import EventStore
from harness.leases import LeaseManager
from harness.recovery import find_orphans, rebuild_state
from harness.states import TechnicalState
from harness.tasks import FailurePolicy, ResourceRequest, Task


def _task():
    return Task(
        task_id="r.task_1", run_id="r", task_type="long", tool_id="sleep",
        command_template="sleep 30", command_argv=["sleep", "30"],
        inputs=["x"], outputs_expected=["out"], validators=["file_exists"],
        resources=ResourceRequest(memory_gb=1),
        failure_policy=FailurePolicy(max_retries=2, timeout_seconds=300),
    )


def test_sigkill_midrun_is_recovered(tmp_path):
    events = EventStore(tmp_path / "e.jsonl")
    lm = LeaseManager(events=events, lease_seconds=10)
    task = _task()
    task.set_technical(TechnicalState.APPROVED)

    # Real child + real lease + real RUNNING transition.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    lm.acquire(task, "worker-doomed", now=0)
    task.set_technical(TechnicalState.RUNNING)
    events.emit("task_started", task_id=task.task_id, worker_id="worker-doomed")

    # The worker dies for real.
    os.kill(child.pid, signal.SIGKILL)
    child.wait(timeout=5)
    assert child.poll() is not None  # actually dead

    # Before reaping: rebuilt state shows an ORPHAN (RUNNING, no terminal event).
    state_before = rebuild_state(events)
    assert state_before[task.task_id].status_technical == TechnicalState.RUNNING
    assert [s.task_id for s in find_orphans(state_before)] == [task.task_id]

    # Reap expired lease -> requeue. No zombie remains.
    requeued = lm.reap_expired({task.task_id: task}, now=100)
    assert task.task_id in requeued
    assert task.status_technical == TechnicalState.REQUEUED

    state_after = rebuild_state(events)
    assert state_after[task.task_id].status_technical == TechnicalState.REQUEUED
    assert find_orphans(state_after) == []
    names = [e["event"] for e in events.read()]
    for required in ("lease_expired", "task_reaped", "task_requeued"):
        assert required in names
