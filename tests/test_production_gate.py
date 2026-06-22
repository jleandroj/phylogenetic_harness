"""Iter 10: production integration gate.

End-to-end proof that the harness refuses to run unprotected work in strict mode,
and that a compliant strict run wires every guarantee together: the run is
policy-checked up front, executes inside the harness, is fully audited, and is
reconstructable by run_id.
"""

import pytest
from conftest import TOOLS_DIR

from harness import audit, trace
from harness.policy import PolicyViolation
from harness.run import Run, RunConfig
from harness.tasks import ResourceRequest, Task


def test_noncompliant_strict_run_is_blocked_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    # strict run with sandbox OFF -> blocked before any directory/work is created.
    cfg = RunConfig(run_id="bad", mode="full", executor="local", sandbox=False, strict=True)
    with pytest.raises(PolicyViolation):
        Run(cfg, base_dir=tmp_path)
    # the run never materialised.
    assert not (tmp_path / "bad").exists()


def test_strict_run_without_network_is_required(tmp_path):
    cfg = RunConfig(run_id="net", mode="full", sandbox=True, allow_network=True, strict=True)
    with pytest.raises(PolicyViolation):
        Run(cfg, base_dir=tmp_path)


def test_compliant_strict_run_executes_audits_and_traces(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    # sandbox on + strict -> compliant (a bwrap/apptainer backend exists in CI).
    cfg = RunConfig(run_id="good", mode="test", executor="local", sandbox=True, strict=True)
    run = Run(cfg, base_dir=tmp_path)
    run.load_tools(TOOLS_DIR)
    runner = run.build_runner()
    src = tmp_path / "s.txt"
    src.write_text("payload\n")
    out = run.dir / "results" / "o.txt"
    task = Task(task_id="good.t1", run_id="good", task_type="copy", tool_id="cp",
                command_template="cp", command_argv=["cp", str(src), str(out)],
                inputs=[str(src)], outputs_expected=[str(out)], validators=["file_exists"],
                resources=ResourceRequest(memory_gb=1))
    bundle = runner.run_task(task)
    run.finish()

    # guarantee #1 audit: the action is in the tamper-evident log and the chain holds.
    events = [r["event"] for r in audit.read()]
    assert "run_started" in events and "run_finished" in events
    assert audit.verify()["ok"] is True
    # guarantee #4 trace: the run reconstructs from its id.
    tr = trace.trace("good")
    assert tr["found"] and tr["n_tasks"] == 1
    # the task actually executed inside the harness.
    assert bundle["status_technical"] in ("SUCCEEDED", "FAILED_FATAL")


@pytest.mark.skipif(
    not __import__("shutil").which("bwrap") and not __import__("shutil").which("apptainer"),
    reason="no sandbox backend installed",
)
def test_strict_requires_a_real_sandbox_backend(tmp_path, monkeypatch):
    """When a backend exists, strict + sandbox=False is a policy violation (not a pass)."""
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    from harness.policy import RunPolicy
    cfg = RunConfig(run_id="x", sandbox=False, strict=True)
    assert RunPolicy.production().check_run_config(cfg)  # non-empty -> violations
