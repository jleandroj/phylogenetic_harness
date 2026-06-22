"""Iter 1-2: central audit log + strict run policy (governance/observability)."""

import json

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


def test_in_harness_tool_call_audited(runner_factory, tmp_path, monkeypatch):
    from harness import audit
    from harness.tasks import ResourceRequest, Task
    log = tmp_path / "a.jsonl"
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(log))
    runner, _, _ = runner_factory()
    src = tmp_path / "s.txt"
    src.write_text("x\n")
    out = tmp_path / "o.txt"
    task = Task(task_id="r.t", run_id="r", task_type="copy", tool_id="cp",
                command_template="cp", command_argv=["cp", str(src), str(out)],
                inputs=[str(src)], outputs_expected=[str(out)], validators=["file_exists"],
                resources=ResourceRequest(memory_gb=1))
    runner.run_task(task)
    tool_calls = [r for r in audit.read() if r["event"] == "tool_call"]
    assert any(r.get("tool") == "cp" and r.get("in_harness") for r in tool_calls)


def test_run_registry_lists_runs(tmp_path, monkeypatch):
    from harness import registry
    from harness.run import Run, RunConfig
    log = tmp_path / "a.jsonl"
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(log))
    r = Run(RunConfig(run_id="regrun", mode="test"), base_dir=tmp_path)
    r.finish()
    runs = registry.list_runs()
    assert any(x["run_id"] == "regrun" and x["finished"] for x in runs)


def test_audit_chain_is_tamper_evident(tmp_path, monkeypatch):
    log = tmp_path / "a.jsonl"
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(log))
    for i in range(5):
        audit.record("x", i=i)
    assert audit.verify()["ok"] is True
    # Tamper: edit a middle line -> chain breaks.
    lines = log.read_text().splitlines()
    rec = json.loads(lines[2])
    rec["i"] = 999
    lines[2] = json.dumps(rec, sort_keys=True)
    log.write_text("\n".join(lines) + "\n")
    v = audit.verify()
    assert v["ok"] is False and v["broken_at"] is not None


def test_autoreport_on_finish_clean_and_anomalous(runner_factory, tmp_path, monkeypatch):
    from harness.run import Run, RunConfig
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    run = Run(RunConfig(run_id="arun", mode="test"), base_dir=tmp_path)
    # one failed bundle on disk -> anomaly
    (run.dir / "results").mkdir(parents=True, exist_ok=True)
    (run.dir / "results" / "t.validation.json").write_text(json.dumps(
        {"task_id": "t", "status_technical": "FAILED_FATAL", "degenerate": False}))
    run.finish()
    assert (run.dir / "RUN_SUMMARY.json").exists()
    assert (run.dir / "ANOMALIES.json").exists()
    anomalies = json.loads((run.dir / "ANOMALIES.json").read_text())
    assert any(a["kind"] == "task_failed" for a in anomalies)
    assert "ALERT" in (run.dir / "ALERT.txt").read_text()
