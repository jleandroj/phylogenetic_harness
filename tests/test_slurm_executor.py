"""SLURMExecutor: sbatch script generation + dry-run submission path are testable
without a real cluster. (A live cluster run is out of scope here.)"""
import shutil
import sys

import pytest

from harness.executor import SLURMExecutor
from harness.tasks import ResourceRequest


def test_sbatch_script_has_directives_and_quoted_argv(tmp_path):
    ex = SLURMExecutor(tmp_path, partition="normal", account="lab")
    script = ex.build_sbatch_script(
        "run.t1", ["iqtree", "-s", "my aln.fa", "-m", "MFP"], attempt=1,
        stdout_path=tmp_path / "o.log", stderr_path=tmp_path / "e.log",
        cpus=4, memory_gb=8, walltime_minutes=120, gpu=True)
    assert script.startswith("#!/bin/bash")
    assert "#SBATCH --cpus-per-task=4" in script
    assert "#SBATCH --mem=8192M" in script
    assert "#SBATCH --time=120" in script
    assert "#SBATCH --partition=normal" in script
    assert "#SBATCH --account=lab" in script
    assert "#SBATCH --gres=gpu:1" in script
    # argv with a space is quoted -> shell-safe, no injection.
    assert "'my aln.fa'" in script
    assert "set -euo pipefail" in script


def test_dry_run_writes_script_without_submitting(tmp_path):
    ex = SLURMExecutor(tmp_path, dry_run=True)
    res = ex.run("run.t2", [sys.executable, "--version"], attempt=1,
                 resources=ResourceRequest(cpus=2, memory_gb=4, walltime_minutes=30))
    assert "dry_run" in res.error
    assert res.exit_code is None
    script = tmp_path / "run.t2.attempt1.sbatch"
    assert script.exists()
    assert "--cpus-per-task=2" in script.read_text()


def test_string_command_rejected(tmp_path):
    from harness.executor import ShellCommandRejected
    ex = SLURMExecutor(tmp_path, dry_run=True)
    with pytest.raises(ShellCommandRejected):
        ex.run("t", "iqtree -s aln")


@pytest.mark.skipif(shutil.which("sbatch") is not None, reason="real SLURM present")
def test_no_sbatch_returns_clear_error(tmp_path):
    ex = SLURMExecutor(tmp_path, dry_run=False)
    res = ex.run("run.t3", [sys.executable, "--version"], attempt=1)
    assert res.exit_code is None
    assert "sbatch not found" in res.error
