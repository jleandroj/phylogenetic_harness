"""Audit Q3: a brand-new task type runs through the SAME TaskRunner with zero
changes to runner.py / scheduler.py — adding a type is one spec + its tool."""
import sys

from harness.task_types import TaskTypeRegistry, TaskTypeSpec, builtin_registry
from harness.tools import ToolContract


def test_builtin_specs_present():
    reg = builtin_registry()
    assert "file_copy" in reg.all()
    assert reg.get("fasta_index").tool_id == "samtools"


def test_new_task_type_runs_unchanged_runner(runner_factory, tmp_path):
    """Register a toy 'reverse_file' task type at runtime and run it through the
    existing runner. This is the entire cost of adding a task type."""
    runner, _, _ = runner_factory()
    # The new type's tool (python) — registered like any other tool contract.
    runner.tools.register(ToolContract(
        tool_id="pytool", tool_name="python", version_command=[sys.executable, "--version"],
    ))

    script = tmp_path / "reverse.py"
    script.write_text(
        "import sys\n"
        "data = open(sys.argv[1]).read()\n"
        "open(sys.argv[2], 'w').write(data[::-1])\n",
        encoding="utf-8",
    )
    src = tmp_path / "in.txt"
    src.write_text("abcdef", encoding="utf-8")
    out = tmp_path / "out.txt"

    # The ONLY new code to add a task type: a spec.
    reg = TaskTypeRegistry()
    reg.register(TaskTypeSpec(
        task_type="reverse_file", tool_id="pytool",
        build_argv=lambda p: [sys.executable, p["script"], p["src"], p["dst"]],
        validators=["file_exists", "file_nonempty"],
    ))

    task = reg.get("reverse_file").build_task(
        task_id="r.rev", run_id="r",
        params={"script": str(script), "src": str(src), "dst": str(out)},
        inputs=[str(src)], outputs_expected=[str(out)],
    )
    bundle = runner.run_task(task)                       # SAME runner, untouched
    assert bundle["status_technical"] == "SUCCEEDED"
    assert out.read_text() == "fedcba"
    assert bundle["task_type"] == "reverse_file"


def test_unknown_task_type_rejected():
    reg = builtin_registry()
    import pytest
    with pytest.raises(KeyError):
        reg.get("does_not_exist")
