import json

from harness.environment import capture_environment, detect_tools


def test_capture_writes_four_artifacts(tmp_path):
    out = tmp_path / "env"
    snap = capture_environment(out, timestamp_iso="2026-06-19T00:00:00-04:00", disk_path=tmp_path)
    for name in (
        "ENVIRONMENT.snapshot.json",
        "ENVIRONMENT.commands.log",
        "ENVIRONMENT.tools.tsv",
        "ENVIRONMENT.hardware.json",
    ):
        assert (out / name).exists(), name
    assert snap["captured_at"] == "2026-06-19T00:00:00-04:00"
    assert "hardware" in snap and "tools" in snap


def test_snapshot_is_valid_json(tmp_path):
    out = tmp_path / "env"
    capture_environment(out, timestamp_iso="t", disk_path=tmp_path)
    data = json.loads((out / "ENVIRONMENT.snapshot.json").read_text())
    assert data["git"]["available"] in (True, False)  # records git presence either way


def test_detect_tools_marks_absent_without_crashing():
    tools = detect_tools()
    # python is essentially always present in this environment
    assert tools["python"]["present"] is True
    # A tool that may be absent must still produce a well-formed record.
    for _name, rec in tools.items():
        assert set(rec) == {"present", "path", "version"}
        if not rec["present"]:
            assert rec["version"] is None and rec["path"] is None
