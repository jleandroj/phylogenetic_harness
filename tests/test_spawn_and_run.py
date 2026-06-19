"""Spawn (not fork) for child fan-out, and full run wiring (spec §24.10/§24.13)."""
import json

from harness.executor import SPAWN_CONTEXT
from harness.run import Run, RunConfig


def test_executor_uses_spawn_context():
    # Spec §24.10: no fork with CUDA. The context must be 'spawn', never 'fork'.
    assert SPAWN_CONTEXT.get_start_method() == "spawn"


def test_runconfig_is_frozen():
    cfg = RunConfig(run_id="r1")
    import dataclasses
    try:
        cfg.seed = 99  # type: ignore[misc]
        assert False, "RunConfig should be immutable"
    except dataclasses.FrozenInstanceError:
        pass


def test_runconfig_rejects_bad_mode():
    import pytest
    with pytest.raises(ValueError):
        RunConfig(run_id="r", mode="nonsense")


def test_run_freezes_config_and_emits_created(tmp_path):
    cfg = RunConfig(run_id="run_xyz", mode="test")
    run = Run(cfg, base_dir=tmp_path)
    assert (run.dir / "RUN_CONFIG.json").exists()
    frozen = json.loads((run.dir / "RUN_CONFIG.json").read_text())
    assert frozen["config_hash"]
    events = run.events.read()
    assert any(e["event"] == "run_created" for e in events)
    run.finish()
    assert any(e["event"] == "run_finished" for e in run.events.read())


def test_run_captures_environment(tmp_path):
    cfg = RunConfig(run_id="run_env", mode="test")
    run = Run(cfg, base_dir=tmp_path)
    run.capture_environment()
    assert (run.dir / "ENVIRONMENT.snapshot.json").exists()
    assert any(e["event"] == "environment_captured" for e in run.events.read())
    run.finish()
