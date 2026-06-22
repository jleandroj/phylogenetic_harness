"""Round 2 / Iter 8: output path confinement — a task cannot overwrite the
read-only genomes, the user's dotfiles, or system files (protected/system roots),
nor escape via '..' traversal.
"""

from harness.confine import check_output_confinement
from harness.tasks import ResourceRequest, Task


def test_confinement_unit(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # legitimate scratch output -> allowed
    assert check_output_confinement([str(run_dir / "results" / "o.txt")]) == []
    # system root -> blocked
    bad = check_output_confinement(["/etc/passwd"])
    assert bad and "system root" in bad[0]
    # operator-protected root (e.g. the read-only genomes) -> blocked
    genomes = tmp_path / "genomes"
    genomes.mkdir()
    prot = check_output_confinement([str(genomes / "human.fa")],
                                    protected_roots=(str(genomes),))
    assert prot and "protected root" in prot[0]
    # traversal -> blocked
    trav = check_output_confinement([str(run_dir / ".." / ".." / "x")])
    assert trav and "traversal" in trav[0]


def test_runner_blocks_write_into_system_root(runner_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    runner, _, _ = runner_factory()
    src = tmp_path / "s.txt"
    src.write_text("x\n")
    victim = "/etc/harness_should_never_write.txt"
    task = Task(task_id="r.t", run_id="r", task_type="copy", tool_id="cp",
                command_template="cp", command_argv=["cp", str(src), victim],
                inputs=[str(src)], outputs_expected=[victim], validators=["file_exists"],
                resources=ResourceRequest(memory_gb=1))
    bundle = runner.run_task(task)
    assert bundle["blocked"] is True and "confinement" in bundle["block_reason"]
    import os
    assert not os.path.exists(victim)


def test_runner_blocks_write_into_protected_genomes(runner_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    genomes = tmp_path / "genomes"
    genomes.mkdir()
    (genomes / "human.fa").write_text(">h\nACGT\n")
    runner, _, _ = runner_factory(protected_roots=(str(genomes),))
    src = tmp_path / "s.txt"
    src.write_text("x\n")
    victim = str(genomes / "human.fa")               # try to clobber the genome
    task = Task(task_id="r.t", run_id="r", task_type="copy", tool_id="cp",
                command_template="cp", command_argv=["cp", str(src), victim],
                inputs=[str(src)], outputs_expected=[victim], validators=["file_exists"],
                resources=ResourceRequest(memory_gb=1))
    bundle = runner.run_task(task)
    assert bundle["blocked"] is True
    assert (genomes / "human.fa").read_text() == ">h\nACGT\n"   # genome untouched
