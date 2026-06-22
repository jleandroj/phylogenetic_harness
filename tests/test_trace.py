"""Iter 8: traceability — reconstruct exactly what happened in a run.

A run leaves three independent records (audit log, event store, result bundles).
``trace`` merges them into one timestamp-ordered timeline so the operator can
replay the run and cross-check the sources against each other.
"""

from conftest import TOOLS_DIR

from harness import audit, trace
from harness.run import Run, RunConfig
from harness.tasks import ResourceRequest, Task


def test_trace_merges_audit_events_and_verdicts(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    run = Run(RunConfig(run_id="tracerun", mode="test"), base_dir=tmp_path)
    run.load_tools(TOOLS_DIR)
    runner = run.build_runner()
    src = tmp_path / "s.txt"
    src.write_text("hello\n")
    out = run.dir / "results" / "o.txt"
    task = Task(task_id="tracerun.t1", run_id="tracerun", task_type="copy", tool_id="cp",
                command_template="cp", command_argv=["cp", str(src), str(out)],
                inputs=[str(src)], outputs_expected=[str(out)], validators=["file_exists"],
                resources=ResourceRequest(memory_gb=1))
    runner.run_task(task)
    run.finish()

    tr = trace.trace("tracerun")
    assert tr["found"] is True
    sources = {it["source"] for it in tr["timeline"]}
    assert {"audit", "events", "bundle"} <= sources
    kinds = {it["kind"] for it in tr["timeline"]}
    assert "run_started" in kinds          # from audit
    assert "VERDICT" in kinds              # from the bundle
    # timeline is timestamp-ordered (non-null ts ascending).
    ts = [it["ts"] for it in tr["timeline"] if it["ts"]]
    assert ts == sorted(ts)
    # the run is rebuildable by directory too.
    assert trace.trace(run.dir)["found"] is True


def test_trace_missing_run_is_reported_not_crashed(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    audit.record("run_started", run_id="other", run_dir=str(tmp_path / "nope"))
    tr = trace.trace("does-not-exist")
    assert tr["found"] is False
    assert "not found" in trace.format_trace(tr)
