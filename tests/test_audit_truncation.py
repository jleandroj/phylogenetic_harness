"""Round 2 / Iter 3: rollback/truncation detection via high-water-mark anchor.

Even a valid hash chain has a hole: an attacker can DELETE the most recent
records and the remaining prefix still verifies. A monotonic seq + a fsync'd
high-water mark anchor makes that truncation detectable.
"""

from harness import audit


def test_truncation_is_detected(tmp_path, monkeypatch):
    log = tmp_path / "a.jsonl"
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(log))
    for i in range(6):
        audit.record("x", i=i)
    assert audit.verify()["ok"] is True

    # Drop the last 2 records — the prefix chain is still internally valid...
    lines = log.read_text().splitlines()
    log.write_text("\n".join(lines[:-2]) + "\n")
    v = audit.verify()
    assert v["ok"] is False                    # ...but the anchor catches the rollback
    assert "high-water mark" in v["reason"]


def test_seq_is_monotonic(tmp_path, monkeypatch):
    import json
    log = tmp_path / "a.jsonl"
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(log))
    for i in range(5):
        audit.record("x", i=i)
    seqs = [json.loads(ln)["seq"] for ln in log.read_text().splitlines()]
    assert seqs == [1, 2, 3, 4, 5]


def test_untruncated_log_still_ok(tmp_path, monkeypatch):
    log = tmp_path / "a.jsonl"
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(log))
    for i in range(3):
        audit.record("x", i=i)
    assert audit.verify()["ok"] is True
