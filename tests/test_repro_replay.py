"""Audit Q1: a frozen run can be replayed; drift is reported, not hidden."""
import json

from harness import manifest, taskstore
from harness.run import Run, RunConfig
from harness.tasks import ResourceRequest, Task


def _do_run(tmp_path, run_id="repro"):
    cfg = RunConfig(run_id=run_id, mode="test", executor="local")
    run = Run(cfg, base_dir=tmp_path)
    run.load_tools(manifest.DEFAULT_TOOLS_DIR)
    run.write_tools_lock()

    src = run.dir / "results" / "src.txt"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("deterministic-content\n", encoding="utf-8")
    out = run.dir / "results" / "out.txt"
    task = Task(
        task_id=f"{run_id}.t1", run_id=run_id, task_type="copy", tool_id="cp",
        command_template="cp", command_argv=["cp", str(src), str(out)],
        inputs=[str(src)], outputs_expected=[str(out)],
        validators=["file_exists", "file_nonempty"], resources=ResourceRequest(memory_gb=1),
    )
    taskstore.save_tasks(run.dir, [task])
    run.build_runner().run_task(task)
    tools_lock = json.loads((run.dir / "TOOLS.lock.json").read_text())
    manifest.write_manifest(
        run.dir,
        run_config={**cfg.to_dict(), "config_hash": cfg.config_hash},
        tools_lock=tools_lock,
        seed_record=run.seeds.record(),
        input_paths=[str(src)],
    )
    run.finish()
    return run.dir


def test_replay_is_identical_for_deterministic_tool(tmp_path):
    run_dir = _do_run(tmp_path)
    report = manifest.replay(run_dir)
    assert report["config_hash_match"] is True
    assert report["tasks"][0]["outputs_match"] is True
    assert report["drift"] == []
    assert report["identical"] is True


def test_replay_reports_drift_when_version_changes(tmp_path):
    run_dir = _do_run(tmp_path, run_id="drift")
    # Tamper the frozen fingerprint to simulate a version change in 3 months.
    mpath = run_dir / manifest.MANIFEST_NAME
    m = json.loads(mpath.read_text())
    m["fingerprint"]["python_version"] = "0.0.0-ancient"
    mpath.write_text(json.dumps(m), encoding="utf-8")

    report = manifest.replay(run_dir)
    assert any("python" in d for d in report["drift"])
    assert report["identical"] is False        # the system does NOT fake reproducibility
    # Outputs can still match byte-for-byte even under drift; that's reported separately.
    assert report["tasks"][0]["outputs_match"] is True


def test_manifest_freezes_provenance(tmp_path):
    run_dir = _do_run(tmp_path, run_id="prov")
    m = json.loads((run_dir / manifest.MANIFEST_NAME).read_text())
    assert m["config_hash"]
    assert m["fingerprint"]["python_version"]
    assert "cp" in m["tools_lock"]
    assert all(v and v.startswith("sha256:") for v in m["inputs"].values())
