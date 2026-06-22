"""Round 2 / Iter 4: secrets on the command line never reach logs/audit/bundles.

A tool invoked as `tool --token sk-XXXX` would otherwise write the token verbatim
into the tamper-proof audit chain, the event store, and the on-disk result bundle.
"""

import json

from harness.redaction import redact_argv


def test_redact_argv_inline_and_separated():
    out = redact_argv(["tool", "--token=sk-abcdef0123456789", "--password", "hunter2", "-x"])
    assert out == ["tool", "--token=<redacted>", "--password", "<redacted>", "-x"]


def test_redact_argv_url_credentials():
    out = redact_argv(["pg_dump", "postgres://user:s3cret@db/x"])
    assert "s3cret" not in " ".join(out)
    assert "user:<redacted>@" in out[1]


def test_argv_redacted_in_audit_and_bundle(runner_factory, tmp_path, monkeypatch):
    from harness import audit
    from harness.tasks import ResourceRequest, Task
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    runner, _, _ = runner_factory()
    src = tmp_path / "s.txt"
    src.write_text("x\n")
    out = tmp_path / "o.txt"
    # cp with a fake secret flag in the argv (cp ignores it after --).
    task = Task(task_id="r.t", run_id="r", task_type="copy", tool_id="cp",
                command_template="cp", command_argv=["cp", "--token=sk-DEADBEEF12345678",
                                                     str(src), str(out)],
                inputs=[str(src)], outputs_expected=[str(out)], validators=["file_exists"],
                resources=ResourceRequest(memory_gb=1))
    bundle = runner.run_task(task)
    # secret not in the audit log
    blob = json.dumps(audit.read())
    assert "sk-DEADBEEF12345678" not in blob
    assert any(r["event"] == "action_finished" for r in audit.read())
    # secret not in the on-disk execution record
    assert "sk-DEADBEEF12345678" not in json.dumps(bundle.get("execution") or {})
