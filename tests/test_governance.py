"""Iter 1-2: central audit log + strict run policy (governance/observability)."""

import pytest

from harness import audit
from harness.policy import PolicyViolation, RunPolicy
from harness.run import Run, RunConfig


def test_audit_record_and_read(tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(log))
    audit.record("run_started", run_id="r1", mode="full")
    audit.record("tool_call", tool="mash", harness_run_id="r1")
    recs = audit.read()
    assert [r["event"] for r in recs] == ["run_started", "tool_call"]
    assert log.exists()


def test_audit_summary_flags_out_of_harness(tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(log))
    monkeypatch.delenv("HARNESS_RUN_ID", raising=False)
    audit.record("tool_call", tool="iqtree")          # no harness_run_id -> outside
    s = audit.summary()
    assert s["tool_calls_OUTSIDE_harness"] == 1


def test_policy_blocks_noncompliant_run_in_strict_mode(tmp_path):
    # strict run without sandbox -> blocked.
    cfg = RunConfig(run_id="strict1", mode="full", executor="local", sandbox=False, strict=True)
    with pytest.raises(PolicyViolation):
        Run(cfg, base_dir=tmp_path)


def test_policy_permissive_passes():
    assert RunPolicy.permissive().check_run_config(
        RunConfig(run_id="x", sandbox=False, allow_network=True)) == []


def test_run_writes_audit_records(tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(log))
    run = Run(RunConfig(run_id="auditrun", mode="test"), base_dir=tmp_path)
    run.finish()
    events = [r["event"] for r in audit.read()]
    assert "run_started" in events and "run_finished" in events
