"""Audit P1.6 / Q2: a run SIGKILLed mid-flight is resumed; unfinished tasks
complete and no orphan remains. The crash is real (a subprocess is SIGKILLed)."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import harness
from harness import taskstore
from harness.resume import resume_run

REPO = Path(harness.__file__).resolve().parent.parent
TOOLS = REPO / "tools"

# A tool that sleeps (until killed) on its first run and is instant afterwards.
SLEEPER = """
import sys, os, time
sentinel, output = sys.argv[1], sys.argv[2]
if not os.path.exists(sentinel):
    open(sentinel, 'w').close()
    time.sleep(60)              # first run: hang here until SIGKILL
with open(output, 'w') as fh:
    fh.write('done')           # resume: instant
"""

# The run program the subprocess executes; it starts a 3-task grid and blocks on
# task 1 (the sleeper) until killed.
RUN_PROG = """
import sys
from pathlib import Path
from harness.run import Run, RunConfig
from harness.scheduler import Scheduler
from harness.taskstore import save_tasks
from harness.tasks import Task, ResourceRequest, FailurePolicy

run_dir = Path(sys.argv[1]); tools = sys.argv[2]; sleeper = sys.argv[3]
cfg = RunConfig(run_id="crash", mode="test", executor="local", output_dir=str(run_dir))
run = Run(cfg, base_dir=run_dir.parent)
run.load_tools(tools)
run.write_tools_lock()
res = run.dir / "results"; res.mkdir(parents=True, exist_ok=True)

def cp_task(i):
    src = res / f"src{i}.txt"; src.write_text(f"c{i}\\n")
    out = res / f"out{i}.txt"
    return Task(task_id=f"crash.t{i}", run_id="crash", task_type="copy", tool_id="cp",
        command_template="cp", command_argv=["cp", str(src), str(out)],
        inputs=[str(src)], outputs_expected=[str(out)], validators=["file_exists", "file_nonempty"],
        resources=ResourceRequest(memory_gb=1), failure_policy=FailurePolicy(retryable=False, max_retries=0))

sentinel = res / "sentinel"; out1 = res / "out1.txt"
t1 = Task(task_id="crash.t1", run_id="crash", task_type="sleepy", tool_id="pysleep",
    command_template="py", command_argv=[sys.executable, sleeper, str(sentinel), str(out1)],
    inputs=[sleeper], outputs_expected=[str(out1)], validators=["file_exists", "file_nonempty"],
    resources=ResourceRequest(memory_gb=1), failure_policy=FailurePolicy(retryable=False, max_retries=0, timeout_seconds=120))

tasks = [cp_task(0), t1, cp_task(2)]
save_tasks(run.dir, tasks)
Scheduler(run.build_runner(), run.dir).run(tasks)
"""


def test_sigkill_then_resume_completes(tmp_path):
    run_dir = tmp_path / "crash"
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text(SLEEPER, encoding="utf-8")
    prog = tmp_path / "runprog.py"
    prog.write_text(RUN_PROG, encoding="utf-8")

    # A shared tools dir with cp + a pysleep contract, so resume can re-register
    # the same tools the original run used (registry is in-memory per process).
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "cp.yaml").write_text((TOOLS / "cp.yaml").read_text(), encoding="utf-8")
    (tools_dir / "pysleep.yaml").write_text(
        "tool_id: pysleep\ntool_name: python\nversion_command: \"python --version\"\n"
        "input_formats: [ANY]\noutput_formats: [ANY]\nvalidators: [exit_code_zero]\n",
        encoding="utf-8",
    )

    env = dict(os.environ, PYTHONPATH=str(REPO))
    proc = subprocess.Popen([sys.executable, str(prog), str(run_dir), str(tools_dir), str(sleeper)], env=env)

    # Wait until task 0 finished and task 1 has started sleeping (sentinel exists).
    sentinel = run_dir / "results" / "sentinel"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if sentinel.exists() and taskstore.is_done(run_dir, "crash.t0"):
            break
        time.sleep(0.1)
    assert sentinel.exists(), "task 1 never started"
    assert taskstore.is_done(run_dir, "crash.t0")

    # Real crash.
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)

    # Mid-crash state: t0 done, t1 interrupted (no bundle), t2 not started.
    assert taskstore.is_done(run_dir, "crash.t0")
    assert not taskstore.is_done(run_dir, "crash.t1")
    assert not taskstore.is_done(run_dir, "crash.t2")

    # Resume completes the remainder (same tools dir the run used).
    summary = resume_run(run_dir, tools_dir=tools_dir)
    assert "crash.t0" in summary["skipped"]            # not re-run
    assert taskstore.is_done(run_dir, "crash.t1")
    assert taskstore.is_done(run_dir, "crash.t2")
    assert summary["orphans_after"] == []              # no zombie remains
