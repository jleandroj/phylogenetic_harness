"""Round 2 / Iter 9: graceful shutdown on signals — no zombies, recorded stop.

A trapped SIGTERM/SIGINT must arm the kill-switch (no new task starts), record
the interruption, and unwind via KeyboardInterrupt so leases/children are cleaned.
"""

import os
import signal
import subprocess
import sys

from harness import audit, killswitch, signals


def test_graceful_stop_arms_killswitch_and_audits(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    res = signals.graceful_stop(run_dir, signum=15)
    stopped, scope = killswitch.is_stopped(run_dir)
    assert stopped and scope == "run"
    assert res["audited"] is True
    assert any(r["event"] == "run_interrupted" for r in audit.read())


def test_context_manager_traps_and_restores(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home2"))
    run_dir = tmp_path / "run2"
    run_dir.mkdir()
    prev = signal.getsignal(signal.SIGTERM)
    raised = False
    try:
        with signals.graceful_shutdown(run_dir):
            os.kill(os.getpid(), signal.SIGTERM)   # delivered to our own handler
    except KeyboardInterrupt:
        raised = True
    assert raised                                   # handler fired and unwound
    assert killswitch.is_stopped(run_dir)[0] is True
    assert signal.getsignal(signal.SIGTERM) is prev  # original handler restored


def test_real_sigterm_subprocess_records_interrupt(tmp_path):
    """End-to-end: a child process trapping SIGTERM records run_interrupted."""
    log = tmp_path / "a.jsonl"
    run_dir = tmp_path / "run3"
    run_dir.mkdir()
    code = (
        "import os, signal, time\n"
        "from harness import signals\n"
        f"rd = {str(run_dir)!r}\n"
        "with signals.graceful_shutdown(rd):\n"
        "    try:\n"
        "        os.kill(os.getpid(), signal.SIGTERM)\n"
        "        time.sleep(2)\n"
        "    except KeyboardInterrupt:\n"
        "        pass\n"
    )
    env = dict(os.environ, HARNESS_AUDIT_LOG=str(log), HARNESS_HOME=str(tmp_path / "h"))
    subprocess.run([sys.executable, "-c", code], env=env, cwd=os.getcwd(), check=True, timeout=30)
    recs = audit.read(log)
    assert any(r["event"] == "run_interrupted" for r in recs)
