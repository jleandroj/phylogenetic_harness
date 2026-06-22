"""Round 2 / Iter 6: safe-by-default resource limits on tool execution.

CORE=0 is always applied (cores can be gigabytes and leak memory=secrets).
FSIZE/NPROC are opt-in caps against disk-fill and fork-bombs. AS stays off by
default so multithreaded JVM tools are not falsely killed.
"""

from harness import clock
from harness.executor import LocalExecutor


def _ex(tmp_path):
    (tmp_path / "logs").mkdir()
    return LocalExecutor(tmp_path / "logs", clock_fn=clock.counting_clock(), disk_path=tmp_path)


def test_core_dumps_disabled_by_default(tmp_path):
    ex = _ex(tmp_path)
    out = tmp_path / "lim.txt"
    # the child prints its core-dump soft limit; must be 0.
    res = ex.run("t", ["bash", "-c", f"ulimit -c > {out}"], attempt=1)
    assert res.exit_code == 0
    assert out.read_text().strip() == "0"


def test_fsize_cap_blocks_disk_fill(tmp_path):
    ex = _ex(tmp_path)
    big = tmp_path / "big.bin"
    # try to write 64MB with a 1MB FSIZE cap -> the write is killed (SIGXFSZ),
    # and the file never reaches 64MB.
    res = ex.run("t", ["bash", "-c", f"dd if=/dev/zero of={big} bs=1M count=64"],
                 attempt=1, rlimit_fsize_gb=1 / 1024)
    assert res.exit_code != 0 or (big.exists() and big.stat().st_size < 8 * 2 ** 20)


def test_normal_tool_unaffected(tmp_path):
    ex = _ex(tmp_path)
    out = tmp_path / "o.txt"
    res = ex.run("t", ["bash", "-c", f"echo ok > {out}"], attempt=1)
    assert res.exit_code == 0 and out.read_text().strip() == "ok"
