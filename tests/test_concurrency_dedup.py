"""Audit-4 #5: two processes running the SAME task (same run-id) must not both
execute it — the per-task flock serialises them and the loser resumes."""
import multiprocessing as mp
import sys
from pathlib import Path

import harness  # noqa: F401  (ensures package importable under spawn)


def _worker(repo: str, base: str, script: str, counter: str, out: str) -> str:
    sys.path.insert(0, repo)
    from harness import clock, taskstore
    from harness.approval import ApprovalGate
    from harness.events import EventStore
    from harness.executor import LocalExecutor
    from harness.leases import LeaseManager
    from harness.runner import TaskRunner
    from harness.seeds import SeedManager
    from harness.tools import ToolContract, ToolRegistry
    from harness.validators import ValidatorRegistry

    base_p = Path(base)
    events = EventStore(base_p / "events" / "e.jsonl", clock=clock.counting_clock())
    tools = ToolRegistry()
    tools.register(ToolContract(tool_id="pytool", tool_name="py",
                                version_command=[sys.executable, "--version"]))
    runner = TaskRunner(
        events=events, tools=tools, validators=ValidatorRegistry(),
        approval=ApprovalGate(events=events),
        executor=LocalExecutor(base_p / "logs", clock_fn=clock.counting_clock(), disk_path=base_p),
        leases=LeaseManager(events=events), results_dir=base_p / "results",
        seeds=SeedManager(42), worker_id="w", clock_fn=clock.monotonic,
    )
    from harness.tasks import FailurePolicy, ResourceRequest, Task
    task = Task(
        task_id="r.shared", run_id="r", task_type="count", tool_id="pytool",
        command_template="py", command_argv=[sys.executable, script, counter, out],
        inputs=[script], outputs_expected=[out],
        validators=["file_exists", "file_nonempty"], resources=ResourceRequest(memory_gb=1),
        failure_policy=FailurePolicy(retryable=False, max_retries=0, timeout_seconds=30),
    )
    b = taskstore.run_or_resume(runner, task)
    return b["status_technical"]


# A tool that sleeps a bit (so the race window is real) then appends to a counter.
SLOW_COUNTER = """
import sys, time
counter, output = sys.argv[1], sys.argv[2]
time.sleep(0.8)
with open(counter, 'a') as fh:
    fh.write('run\\n')
with open(output, 'w') as fh:
    fh.write('done')
"""


def test_two_processes_same_task_execute_once(tmp_path):
    repo = str(Path(harness.__file__).resolve().parent.parent)
    base = tmp_path / "run"
    (base / "results").mkdir(parents=True)
    script = tmp_path / "slow.py"
    script.write_text(SLOW_COUNTER, encoding="utf-8")
    counter = tmp_path / "counter.txt"
    out = tmp_path / "out.txt"

    ctx = mp.get_context("spawn")
    args = (repo, str(base), str(script), str(counter), str(out))
    procs = [ctx.Process(target=_worker, args=args) for _ in range(2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    # Despite two concurrent runners on the same task, it executed EXACTLY once.
    assert counter.read_text().count("run") == 1
    assert out.exists()
