import pytest

from harness.approval import Approval, ApprovalError, ApprovalGate
from harness.events import EventStore
from harness.tasks import ResourceRequest, Task


def make_task(**kw):
    defaults = dict(
        task_id="t1", run_id="r1", task_type="x", tool_id="echo",
        command_template="echo {x}", inputs=["i"], outputs_expected=["o"],
        validators=["file_exists"], params={"x": "1"},
    )
    defaults.update(kw)
    return Task(**defaults)


def test_harmless_task_needs_no_approval(tmp_path):
    gate = ApprovalGate(events=EventStore(tmp_path / "e.jsonl"))
    gate.check(make_task())  # does not raise


def test_required_task_blocks_without_grant(tmp_path):
    gate = ApprovalGate(events=EventStore(tmp_path / "e.jsonl"))
    task = make_task(requires_approval=True)
    with pytest.raises(ApprovalError):
        gate.check(task)


def test_required_task_runs_after_grant(tmp_path):
    gate = ApprovalGate(events=EventStore(tmp_path / "e.jsonl"))
    task = make_task(requires_approval=True)
    gate.grant(Approval(task_id="t1", granted=True, approved_by="leandro", reason="ok"))
    gate.check(task)  # no raise


def test_gpu_task_auto_flagged(tmp_path):
    gate = ApprovalGate(events=EventStore(tmp_path / "e.jsonl"))
    task = make_task(resources=ResourceRequest(gpu=True))
    assert "uses GPU" in gate.auto_flags(task)
    with pytest.raises(ApprovalError):
        gate.check(task)


def test_big_ram_auto_flagged(tmp_path):
    gate = ApprovalGate(events=EventStore(tmp_path / "e.jsonl"), host_memory_gb=100)
    task = make_task(resources=ResourceRequest(memory_gb=80))
    assert gate.needs_approval(task)


def test_denied_grant_still_blocks(tmp_path):
    gate = ApprovalGate(events=EventStore(tmp_path / "e.jsonl"))
    task = make_task(requires_approval=True)
    gate.grant(Approval(task_id="t1", granted=False, reason="too costly"))
    with pytest.raises(ApprovalError):
        gate.check(task)
