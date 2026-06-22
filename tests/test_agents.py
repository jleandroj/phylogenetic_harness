"""Multi-agent verification layer: honesty-first gating.

A biological conclusion (PASS) is allowed ONLY when the gating agents are
explicitly PASS — never on the absence of a failure. These tests pin the
honest downgrades (UNKNOWN / NOT_TESTED / NOT_REPRODUCIBLE / *_EVIDENCE).
"""

import json

import pytest

from harness.agents import (
    AgentContext,
    AgentStatus,
    AncestorValidationAgent,
    FactGuardAgent,
    FinalStatus,
    RedTeamAuditorAgent,
    ReproducibilityAgent,
    SecurityAgent,
    verify_run,
)


def _mkrun(tmp_path, bundles, *, config=None, claims=None):
    rd = tmp_path / "run"
    (rd / "results").mkdir(parents=True, exist_ok=True)
    (rd / "RUN_CONFIG.json").write_text(json.dumps(config or {"run_id": "r", "sandbox": True}))
    for b in bundles:
        (rd / "results" / f"{b['task_id']}.validation.json").write_text(json.dumps(b))
    if claims is not None:
        (rd / "CLAIMS.json").write_text(json.dumps(claims))
    return rd


def _bundle(task_id="r.t1", *, tech="SUCCEEDED", sci="BIOLOGICALLY_INTERPRETABLE",
            validators_passed=True, degenerate=False, outputs=None, inputs_sha256=None):
    return {"task_id": task_id, "task_type": "x", "tool_id": "cp",
            "status_technical": tech, "status_scientific": sci,
            "validators_passed": validators_passed, "degenerate": degenerate,
            "outputs": outputs or [], "inputs_sha256": inputs_sha256 or {},
            "execution": {"exit_code": 0, "inputs": []}, "validation": []}


def test_factguard_fails_unbacked_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    rd = _mkrun(tmp_path, [_bundle()], claims=[{"statement": "X is under selection"}])
    ctx = AgentContext.load(rd)
    v = FactGuardAgent().check(ctx)
    assert v.status == AgentStatus.FAIL


def test_factguard_passes_backed_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    rd = _mkrun(tmp_path, [_bundle()],
                claims=[{"statement": "tree topology T", "evidence": ["results/r.t1.nwk"]}])
    v = FactGuardAgent().check(AgentContext.load(rd))
    assert v.status == AgentStatus.PASS


def test_ancestor_used_as_observed_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    b = _bundle()
    b["validation"] = [{"name": "observed_taxa_only", "status": "FAILED"}]
    rd = _mkrun(tmp_path, [b])
    v = AncestorValidationAgent().check(AgentContext.load(rd))
    assert v.status == AgentStatus.FAIL


def test_reproducibility_not_tested_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    rd = _mkrun(tmp_path, [_bundle()])
    v = ReproducibilityAgent().check(AgentContext.load(rd))
    assert v.status == AgentStatus.NOT_TESTED          # never assume reproducible


def test_reproducibility_flags_non_determinism(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    rd = _mkrun(tmp_path, [_bundle()])
    (rd / "NON_DETERMINISTIC_WARNING.txt").write_text("cactus ancestor unstable\n")
    v = ReproducibilityAgent().check(AgentContext.load(rd))
    assert v.status == AgentStatus.NOT_REPRODUCIBLE


def test_redteam_catches_masked_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    rd = _mkrun(tmp_path, [_bundle(tech="SUCCEEDED", validators_passed=False)])
    v = RedTeamAuditorAgent().check(AgentContext.load(rd))
    assert v.status == AgentStatus.FAIL


def test_security_fails_on_escaping_output(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    b = _bundle(outputs=[{"path": "/etc/passwd", "sha256": "sha256:x"}])
    rd = _mkrun(tmp_path, [b])
    v = SecurityAgent().check(AgentContext.load(rd))
    assert v.status == AgentStatus.FAIL


def test_coordinator_pass_requires_all_bio_gates(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    # a clean, fully-evidenced, reproduced run
    out = {"path": str(tmp_path / "run" / "results" / "tree.nwk"), "sha256": "sha256:abc"}
    b = _bundle(outputs=[out])
    rd = _mkrun(tmp_path, [b],
                claims=[{"statement": "topology", "evidence": ["results/tree.nwk"]}])
    (rd / "results" / "tree.nwk").write_text("(A,B,C);\n")
    # simulate a matching replay so ReproducibilityAgent can PASS
    replay = tmp_path / "run_replay"
    (replay / "results").mkdir(parents=True)
    monkeypatch.setattr("harness.diff.diff_runs", lambda a, c: {"result_drift": None})
    decision = verify_run(rd)
    # Reproducibility now PASS, others PASS -> biological conclusion allowed
    assert decision["status"] in (FinalStatus.PASS.value, FinalStatus.PASS_EXPLORATORY.value)


def test_coordinator_writes_report_and_audits(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    rd = _mkrun(tmp_path, [_bundle()])
    decision = verify_run(rd)
    assert (rd / "VERIFICATION.json").exists()
    assert (rd / "VERIFICATION.md").exists()
    assert decision["status"] in {s.value for s in FinalStatus}
    from harness import audit
    assert any(r["event"] == "verification_decision" for r in audit.read())


def test_empty_run_is_unknown_not_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    rd = _mkrun(tmp_path, [])
    decision = verify_run(rd)
    assert decision["status"] != FinalStatus.PASS.value
    assert decision["allow_biological_conclusion"] is False


@pytest.mark.parametrize("agent_cls", [a for a in __import__(
    "harness.agents", fromlist=["AGENT_ORDER"]).AGENT_ORDER])
def test_agents_never_raise(tmp_path, monkeypatch, agent_cls):
    monkeypatch.setenv("HARNESS_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    rd = _mkrun(tmp_path, [_bundle()])
    v = agent_cls().check(AgentContext.load(rd))      # must return a Verdict, never raise
    assert v.status in set(AgentStatus)
