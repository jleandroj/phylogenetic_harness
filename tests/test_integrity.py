"""Round 2 / Iter 10: input integrity — refuse to run on mutated source data.

If a declared input's bytes changed from the recorded baseline (silent corruption,
accidental overwrite, tampering of the read-only genomes), the harness must block
the run and alert, not produce science from data it cannot trust.
"""

from harness.integrity import hash_inputs, verify_inputs
from harness.tasks import ResourceRequest, Task


def test_verify_inputs_detects_change(tmp_path):
    f = tmp_path / "genome.fa"
    f.write_text(">h\nACGT\n")
    baseline = hash_inputs([str(f)])
    assert verify_inputs([str(f)], baseline) == []          # unchanged -> ok
    f.write_text(">h\nACGA\n")                              # one base flips
    bad = verify_inputs([str(f)], baseline)
    assert bad and "changed since baseline" in bad[0]


def test_runner_blocks_when_input_mutated(runner_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    runner, _, _ = runner_factory()
    src = tmp_path / "genome.fa"
    src.write_text(">h\nACGT\n")
    baseline = hash_inputs([str(src)])
    src.write_text(">h\nTTTT\n")                            # mutated after baseline
    out = tmp_path / "o.txt"
    task = Task(task_id="r.t", run_id="r", task_type="copy", tool_id="cp",
                command_template="cp", command_argv=["cp", str(src), str(out)],
                inputs=[str(src)], outputs_expected=[str(out)], validators=["file_exists"],
                resources=ResourceRequest(memory_gb=1),
                params={"expected_input_sha256": {k: v for k, v in baseline.items()}})
    bundle = runner.run_task(task)
    assert bundle["blocked"] is True
    assert "integrity" in bundle["block_reason"]
    assert not out.exists()


def test_bundle_records_input_hashes(runner_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    runner, _, _ = runner_factory()
    src = tmp_path / "in.txt"
    src.write_text("data\n")
    out = tmp_path / "o.txt"
    task = Task(task_id="r.t", run_id="r", task_type="copy", tool_id="cp",
                command_template="cp", command_argv=["cp", str(src), str(out)],
                inputs=[str(src)], outputs_expected=[str(out)], validators=["file_exists"],
                resources=ResourceRequest(memory_gb=1))
    bundle = runner.run_task(task)
    assert bundle["inputs_sha256"][str(src)].startswith("sha256:")
