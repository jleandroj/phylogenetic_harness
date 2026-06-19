"""Lease expiry, reaping, requeue and worker loss (spec §15, §24.11)."""
from harness.events import EventStore
from harness.leases import LeaseManager
from harness.states import TechnicalState
from harness.tasks import FailurePolicy, ResourceRequest, Task
from harness.workers import WorkerManager


def make_task(retryable_retries=2):
    t = Task(
        task_id="run1.task_000001",
        run_id="run1",
        task_type="demo",
        tool_id="echo",
        command_template="echo {x}",
        inputs=["in"],
        outputs_expected=["out"],
        validators=["file_exists"],
        resources=ResourceRequest(),
        failure_policy=FailurePolicy(max_retries=retryable_retries),
        params={"x": "1"},
    )
    t.set_technical(TechnicalState.APPROVED)
    return t


def test_expired_lease_requeues_and_is_not_zombie(tmp_path):
    events = EventStore(tmp_path / "e.jsonl")
    lm = LeaseManager(events=events, lease_seconds=10)
    task = make_task()
    lm.acquire(task, "worker-1", now=0)
    task.set_technical(TechnicalState.RUNNING)

    # Worker dies; we advance time past the lease and reap.
    requeued = lm.reap_expired({task.task_id: task}, now=100)
    assert task.task_id in requeued
    # Not a zombie: it is back to REQUEUED, ready to be leased again.
    assert task.status_technical == TechnicalState.REQUEUED
    assert task.retries == 1

    names = [e["event"] for e in events.read()]
    for required in ("lease_expired", "task_reaped", "task_requeued", "recovery_completed"):
        assert required in names


def test_retries_exhausted_goes_fatal(tmp_path):
    events = EventStore(tmp_path / "e.jsonl")
    lm = LeaseManager(events=events, lease_seconds=10)
    task = make_task(retryable_retries=0)
    lm.acquire(task, "w", now=0)
    task.set_technical(TechnicalState.RUNNING)
    lm.reap_expired({task.task_id: task}, now=50)
    assert task.status_technical == TechnicalState.FAILED_FATAL


def test_unexpired_lease_not_reaped(tmp_path):
    lm = LeaseManager(lease_seconds=100)
    task = make_task()
    lm.acquire(task, "w", now=0)
    task.set_technical(TechnicalState.RUNNING)
    assert lm.reap_expired({task.task_id: task}, now=10) == []
    assert task.status_technical == TechnicalState.RUNNING


def test_worker_loss_detected(tmp_path):
    events = EventStore(tmp_path / "e.jsonl")
    wm = WorkerManager(events=events, heartbeat_timeout=30)
    wm.start("w1", now=0)
    wm.heartbeat("w1", now=10)
    assert wm.detect_lost(now=20) == []      # still within timeout
    lost = wm.detect_lost(now=100)            # heartbeat aged out
    assert [w.worker_id for w in lost] == ["w1"]
    assert "w1" not in wm.alive_workers()
    assert any(e["event"] == "worker_lost" for e in events.read())
