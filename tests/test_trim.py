"""Audit-4 #6: alignment trimming removes gappy columns; the pipeline builds the
tree on the trimmed alignment; a degenerate (all-gap) alignment is caught."""
from harness.tasks import FailurePolicy, ResourceRequest, Task
from harness.tools import ToolContract
from harness.trimtool import trim_alignment


def test_trim_removes_gappy_columns(tmp_path):
    # 4 sequences; column 2 is mostly gaps (3/4), the rest are clean.
    aln = tmp_path / "in.fa"
    aln.write_text(
        ">a\nAC-GT\n>b\nAC-GT\n>c\nAC-GT\n>d\nACAGT\n", encoding="utf-8")
    out = tmp_path / "out.fa"
    stats = trim_alignment(aln, out, max_gap_fraction=0.5)
    assert stats["columns_before"] == 5
    assert stats["columns_after"] == 4          # the 3/4-gap column removed
    assert stats["removed"] == 1
    # All sequences keep equal (trimmed) length.
    seqs = [ln for ln in out.read_text().splitlines() if not ln.startswith(">")]
    assert all(len(s) == 4 for s in seqs)


def test_trim_keeps_clean_alignment(tmp_path):
    aln = tmp_path / "in.fa"
    aln.write_text(">a\nACGT\n>b\nACGT\n", encoding="utf-8")
    out = tmp_path / "out.fa"
    stats = trim_alignment(aln, out, max_gap_fraction=0.5)
    assert stats["removed"] == 0


def test_trim_task_runs_through_runner(runner_factory, tmp_path):
    runner, _, tools = runner_factory()
    assert tools.get("trimmer").available   # built-in trimmer is python -> available
    aln = tmp_path / "in.fa"
    aln.write_text(">a\nAC--GT\n>b\nAC--GT\n>c\nACAAGT\n", encoding="utf-8")
    out = tmp_path / "out.fa"
    task = Task(
        task_id="r.trim", run_id="r", task_type="trim_alignment", tool_id="trimmer",
        command_template="trim",
        command_argv=["python", "-m", "harness.trimtool", str(aln), str(out), "0.5"],
        inputs=[str(aln)], outputs_expected=[str(out)], validators=["alignment_valid"],
        resources=ResourceRequest(memory_gb=1),
        failure_policy=FailurePolicy(retryable=False, max_retries=0, timeout_seconds=60),
    )
    runner.tools.register(ToolContract(tool_id="trimmer", tool_name="t",
                                       version_command=["python", "--version"]))
    bundle = runner.run_task(task)
    assert bundle["status_technical"] == "SUCCEEDED"
    assert out.exists()


def test_all_gap_alignment_trims_to_degenerate(tmp_path):
    # Every column is mostly gaps -> trimmed alignment is empty/degenerate.
    aln = tmp_path / "in.fa"
    aln.write_text(">a\n----\n>b\n----\n>c\nAC GT\n".replace(" ", "-"), encoding="utf-8")
    out = tmp_path / "out.fa"
    stats = trim_alignment(aln, out, max_gap_fraction=0.5)
    assert stats["columns_after"] == 0          # nothing survives -> downstream validators fail
