"""Audit P2.12: golden report, approvals persistence, allow_overwrite wiring."""
import json
from pathlib import Path

from harness.approval import Approval, ApprovalGate
from harness.events import EventStore
from harness.report import ReportGenerator
from harness.tasks import ResourceRequest, Task

GOLDEN = Path(__file__).resolve().parent / "golden" / "report_min.md"


def _golden_sections():
    sections = ReportGenerator.empty_sections()
    sections["1. What was executed"] = ["task demo: cp src dst -> SUCCEEDED"]
    sections["6. What was technically valid"] = ["file_exists: PASSED", "file_nonempty: PASSED"]
    sections["8. What CANNOT be concluded"] = [
        "Technical success does not imply biological correctness."
    ]
    return sections


def test_report_matches_golden(tmp_path):
    gen = ReportGenerator(tmp_path)
    gen.generate({
        "run_id": "golden_run", "summary": "Golden snapshot.",
        "scientific_question": "none", "sections": _golden_sections(),
    })
    assert (tmp_path / "report.md").read_text() == GOLDEN.read_text()


def test_approvals_persisted(tmp_path):
    path = tmp_path / "approvals.json"
    gate = ApprovalGate(events=EventStore(tmp_path / "e.jsonl"), persist_path=path)
    gate.grant(Approval(task_id="t1", granted=True, approved_by="leandro", reason="ok"))
    data = json.loads(path.read_text())
    assert data["t1"]["granted"] is True and data["t1"]["approved_by"] == "leandro"


def _task(out_path):
    return Task(
        task_id="t1", run_id="r", task_type="x", tool_id="cp",
        command_template="cp a b", command_argv=["cp", "a", str(out_path)],
        inputs=["a"], outputs_expected=[str(out_path)], validators=["file_exists"],
        resources=ResourceRequest(memory_gb=1),
    )


def test_overwrite_requires_approval_when_disallowed(tmp_path):
    existing = tmp_path / "out.txt"
    existing.write_text("already here\n")
    gate = ApprovalGate(events=EventStore(tmp_path / "e.jsonl"), allow_overwrite=False)
    assert gate.needs_approval(_task(existing)) is True


def test_overwrite_allowed_when_permitted(tmp_path):
    existing = tmp_path / "out.txt"
    existing.write_text("already here\n")
    gate = ApprovalGate(events=EventStore(tmp_path / "e.jsonl"), allow_overwrite=True)
    assert gate.needs_approval(_task(existing)) is False
