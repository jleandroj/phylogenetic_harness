"""Audit P0.4/P0.5: oversized stdout is capped, not allowed to fill the disk."""
import sys

from harness.clock import counting_clock
from harness.executor import LocalExecutor

PY = sys.executable
CAP = 64 * 1024  # 64 KiB cap for the test


def test_giant_stdout_is_truncated(run_dir):
    ex = LocalExecutor(run_dir, clock_fn=counting_clock(), disk_path=run_dir, output_cap_bytes=CAP)
    # Child writes ~5 MB to stdout.
    res = ex.run("t", [PY, "-c", "import sys; sys.stdout.write('x'*5_000_000)"])
    assert res.exit_code == 0
    assert res.truncated_stdout is True
    size = (run_dir / "t.attempt1.stdout.log").stat().st_size
    # Capped output plus a short truncation marker — far below 5 MB.
    assert size <= CAP + 256


def test_normal_stdout_not_truncated(run_dir):
    ex = LocalExecutor(run_dir, clock_fn=counting_clock(), disk_path=run_dir, output_cap_bytes=CAP)
    res = ex.run("t2", ["echo", "small"])
    assert res.truncated_stdout is False
