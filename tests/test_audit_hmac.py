"""Round 2 / Iter 2: HMAC-signed audit chain — tamper-PROOF, not just evident.

With a key set, an attacker who can write the log cannot recompute a valid chain
after editing a record (unlike the plain sha256 chain). The key is stripped from
every child tool's environment, so a malicious tool cannot read it.
"""

import json

from harness import audit


def test_keyed_chain_cannot_be_silently_recomputed(tmp_path, monkeypatch):
    log = tmp_path / "a.jsonl"
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(log))
    monkeypatch.setenv("HARNESS_AUDIT_KEY", "operator-secret-key")
    for i in range(4):
        audit.record("x", i=i)
    v = audit.verify()
    assert v["ok"] is True and v["keyed"] is True

    # Attacker edits a record AND recomputes the downstream chain with sha256
    # (no key). A keyed verify must still reject it.
    import hashlib
    lines = log.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["i"] = 999
    lines[1] = json.dumps(rec, sort_keys=True)
    expected = audit._chain_mac((lines[0] + "\n").encode())  # genuine first link
    for j in range(1, len(lines)):
        r = json.loads(lines[j])
        r["prev"] = expected
        lines[j] = json.dumps(r, sort_keys=True)
        # forge with plain sha256, lacking the key
        expected = "hmac:" + hashlib.sha256((lines[j] + "\n").encode()).hexdigest()
    log.write_text("\n".join(lines) + "\n")
    assert audit.verify()["ok"] is False           # forgery detected


def test_child_env_strips_audit_key(tmp_path, monkeypatch):
    from harness import clock
    from harness.executor import LocalExecutor
    monkeypatch.setenv("HARNESS_AUDIT_KEY", "secret")
    (tmp_path / "logs").mkdir()
    ex = LocalExecutor(tmp_path / "logs", clock_fn=clock.counting_clock(), disk_path=tmp_path)
    out = tmp_path / "env.txt"
    # `env` writes the child environment; the key must NOT appear.
    res = ex.run("t", ["bash", "-c", f"env > {out}"], attempt=1)
    assert res is not None
    body = out.read_text()
    assert "HARNESS_AUDIT_KEY" not in body
