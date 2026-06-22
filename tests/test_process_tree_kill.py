"""Round 2 / Iter 5: a timeout reaps the WHOLE process tree (no orphans escape).

A tool that backgrounds a grandchild must not leave it running after the harness
times the tool out — otherwise work escapes containment and burns resources.
"""

import os
import time

from harness import clock
from harness.executor import LocalExecutor


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_timeout_kills_grandchild(tmp_path):
    (tmp_path / "logs").mkdir()
    ex = LocalExecutor(tmp_path / "logs", clock_fn=clock.counting_clock(), disk_path=tmp_path)
    gc = tmp_path / "grandchild.pid"
    # Parent backgrounds a long sleep (the "grandchild"), records its pid, then
    # blocks. The 1s timeout must reap both.
    script = f"sleep 60 & echo $! > {gc}; sleep 60"
    res = ex.run("t", ["bash", "-c", script], timeout_seconds=1, attempt=1)
    assert res.timed_out is True

    pid = int(gc.read_text().strip())
    # give SIGTERM->SIGKILL a moment to propagate through the group
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(pid), f"grandchild {pid} survived the timeout (orphan escaped)"
