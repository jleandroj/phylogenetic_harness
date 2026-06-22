"""Round 2 / Iter 1: fail-closed bitácora — no durable audit ⇒ no execution.

A bad-faith agent that makes the audit log unwritable (fills the disk, drops
write permission) must not be able to run un-logged work.
"""

import json

from harness import audit
from harness.tasks import ResourceRequest, Task


def _cp_task(tmp_path):
    src = tmp_path / "s.txt"
    src.write_text("x\n")
    out = tmp_path / "o.txt"
    return Task(task_id="r.t", run_id="r", task_type="copy", tool_id="cp",
                command_template="cp", command_argv=["cp", str(src), str(out)],
                inputs=[str(src)], outputs_expected=[str(out)], validators=["file_exists"],
                resources=ResourceRequest(memory_gb=1)), out


def test_unwritable_audit_blocks_execution(runner_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    # Point the audit log at a path that cannot be created (parent is a file).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir\n")
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(blocker / "audit.jsonl"))
    runner, _, _ = runner_factory()
    task, out = _cp_task(tmp_path)
    bundle = runner.run_task(task)
    assert bundle["blocked"] is True
    assert "audit log unavailable" in bundle["block_reason"]
    assert not out.exists()                      # the tool never ran


def test_record_is_durable_and_fsynced(tmp_path, monkeypatch):
    log = tmp_path / "a.jsonl"
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(log))
    audit.ensure_writable()
    audit.record("probe", k="v")
    recs = [json.loads(ln) for ln in log.read_text().splitlines()]
    assert recs[-1]["event"] == "probe" and recs[-1]["k"] == "v"
