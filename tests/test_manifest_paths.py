"""Audit P2.9: manifest paths are confined; GPU pinning is resolved, not assumed."""
import pytest

from harness.datasets import DatasetInput, ManifestError


def _input(path):
    return {"sample_id": "s", "path": path, "format": "FASTA"}


def test_absolute_path_rejected():
    with pytest.raises(ManifestError):
        DatasetInput.from_dict(_input("/etc/passwd"))


def test_parent_traversal_rejected():
    with pytest.raises(ManifestError):
        DatasetInput.from_dict(_input("../../secrets.txt"))


def test_relative_path_accepted(tmp_path):
    (tmp_path / "g.fa").write_text(">a\nACGT\n")
    di = DatasetInput.from_dict(_input("g.fa"))
    di.compute_checksum(tmp_path)
    assert di.checksum.startswith("sha256:")


def test_gpu_resolution_returns_none_without_gpu_request(runner_factory):
    runner, _, _ = runner_factory()
    from harness.tasks import ResourceRequest, Task
    task = Task(
        task_id="t", run_id="r", task_type="x", tool_id="cp",
        command_template="cp a b", command_argv=["cp", "a", "b"],
        inputs=["a"], outputs_expected=["b"], validators=["file_exists"],
        resources=ResourceRequest(gpu=False),
    )
    assert runner._gpu_for(task) is None  # never assumes device "0"
