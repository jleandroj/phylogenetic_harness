"""Iter 7: containment — kill-switch + action allowlist (production guarantee #3).

The harness must abort a misbehaving/killed run cleanly (no execution, no crash)
and refuse any tool not on an explicit allowlist. Never assume the agent's good faith.
"""

from harness import killswitch
from harness.tasks import ResourceRequest, Task


def _copy_task(tmp_path, tool_id="cp"):
    src = tmp_path / "s.txt"
    src.write_text("x\n")
    out = tmp_path / "o.txt"
    return Task(task_id="r.t", run_id="r", task_type="copy", tool_id=tool_id,
                command_template="cp", command_argv=["cp", str(src), str(out)],
                inputs=[str(src)], outputs_expected=[str(out)], validators=["file_exists"],
                resources=ResourceRequest(memory_gb=1)), out


def test_killswitch_aborts_task_without_executing(runner_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))   # isolate global STOP
    runner, _, _ = runner_factory()
    task, out = _copy_task(tmp_path)
    # run_dir is results_dir.parent; drop a per-run STOP marker there.
    killswitch.stop(runner.results_dir.parent)
    bundle = runner.run_task(task)
    assert bundle["status_technical"] == "FAILED_FATAL"
    assert bundle["blocked"] is True
    assert "kill-switch" in bundle["block_reason"]
    assert not out.exists()                       # the tool never ran


def test_panic_stops_all_runs(runner_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    runner, _, _ = runner_factory()
    task, out = _copy_task(tmp_path)
    killswitch.panic()
    bundle = runner.run_task(task)
    assert bundle["blocked"] and "global" in bundle["block_reason"]
    assert not out.exists()


def test_allowlist_blocks_disallowed_tool(runner_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))   # no global STOP
    runner, _, _ = runner_factory(tool_allowlist=["mafft"])      # cp NOT allowed
    task, out = _copy_task(tmp_path)
    bundle = runner.run_task(task)
    assert bundle["blocked"] and "allowlist" in bundle["block_reason"]
    assert not out.exists()


def test_allowlist_permits_listed_tool(runner_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    runner, _, _ = runner_factory(tool_allowlist=["cp"])
    task, out = _copy_task(tmp_path)
    bundle = runner.run_task(task)
    assert bundle.get("blocked") is not True
    assert out.exists()


def test_blocked_task_is_audited(runner_factory, tmp_path, monkeypatch):
    from harness import audit
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    runner, _, _ = runner_factory(tool_allowlist=[])
    task, _ = _copy_task(tmp_path)
    runner.run_task(task)
    assert any(r["event"] == "action_blocked" for r in audit.read())
