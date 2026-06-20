"""Audit-4 #3: the sandbox is EXERCISED with a real bwrap namespace through the
harness executor — it runs binaries, hides the host /tmp, and blocks the network.
Skips cleanly if bwrap is absent."""
import shutil
import sys

import pytest

from harness.clock import counting_clock
from harness.executor import LocalExecutor
from harness.sandbox import wrap

pytestmark = pytest.mark.skipif(not shutil.which("bwrap"), reason="bwrap not installed")


def _exec(run_dir):
    return LocalExecutor(run_dir, clock_fn=counting_clock(), disk_path=run_dir)


def test_sandboxed_command_runs(run_dir):
    argv, mode = wrap(["printf", "%s", "ok-sandboxed"], enabled=True, backend="bwrap")
    assert mode == "bwrap"
    res = _exec(run_dir).run("t1", argv)
    assert res.exit_code == 0
    assert "ok-sandboxed" in open(res.stdout_path).read()


def test_sandbox_hides_host_tmp(run_dir, tmp_path):
    secret = tmp_path / "secret.txt"           # under /tmp on the host
    secret.write_text("top-secret\n")
    argv, _ = wrap(["cat", str(secret)], enabled=True, backend="bwrap")
    res = _exec(run_dir).run("t2", argv)
    assert res.exit_code != 0                   # fresh tmpfs /tmp -> file not visible
    assert "top-secret" not in open(res.stdout_path).read()


def test_sandbox_blocks_network(run_dir):
    code = "import socket; socket.create_connection(('8.8.8.8', 53), 2)"
    argv, _ = wrap([sys.executable, "-c", code], enabled=True, backend="bwrap")
    res = _exec(run_dir).run("t3", argv)
    assert res.exit_code != 0                   # --unshare-net -> network unreachable


def test_writable_bind_allows_output(run_dir, tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    out = workdir / "out.txt"
    argv, _ = wrap([sys.executable, "-c", f"open({str(out)!r},'w').write('done')"],
                   enabled=True, backend="bwrap", binds=[str(workdir)])
    res = _exec(run_dir).run("t4", argv)
    assert res.exit_code == 0
    assert out.read_text() == "done"            # writable bind let the output through


def test_executor_sandbox_config_runs_and_binds(run_dir, tmp_path):
    """LocalExecutor with sandbox enabled wraps the command, writes to a bound
    dir, and records the backend mode."""
    work = tmp_path / "work"
    work.mkdir()
    out = work / "out.txt"
    ex = LocalExecutor(run_dir, clock_fn=counting_clock(), disk_path=run_dir,
                       sandbox={"enabled": True, "backend": "bwrap", "binds": [str(work)]})
    res = ex.run("t", [sys.executable, "-c", f"open({str(out)!r},'w').write('sandboxed')"])
    assert res.exit_code == 0
    assert out.read_text() == "sandboxed"     # writable bind worked
    assert ex._sandbox_mode == "bwrap"         # the wrap actually applied


def test_executor_sandbox_blocks_network(run_dir):
    ex = LocalExecutor(run_dir, clock_fn=counting_clock(), disk_path=run_dir,
                       sandbox={"enabled": True, "backend": "bwrap", "binds": []})
    code = "import socket; socket.create_connection(('8.8.8.8', 53), 2)"
    res = ex.run("t2", [sys.executable, "-c", code])
    assert res.exit_code != 0                  # no network inside the sandbox
