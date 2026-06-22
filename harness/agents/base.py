"""Multi-agent verification layer — shared base (honesty-first).

The harness must never confuse execution with truth. Every agent returns a
``Verdict`` whose status is drawn from a deliberately *honest* vocabulary: when an
agent cannot actually verify something, it says so (UNKNOWN / NOT_TESTED /
INSUFFICIENT_EVIDENCE) instead of defaulting to PASS. The CoordinatorAgent then
refuses to allow a biological conclusion unless the gating agents are explicitly
PASS — never on the absence of a failure.

An agent NEVER raises into the coordinator: an internal error becomes an UNKNOWN
verdict (fault isolation), so one broken agent cannot crash the verification or
silently let a run through.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class AgentStatus(str, Enum):
    PASS = "PASS"                                   # verified true, with evidence
    FAIL = "FAIL"                                   # verified false / violation
    UNKNOWN = "UNKNOWN"                             # could not determine
    NOT_TESTED = "NOT_TESTED"                       # nothing to test / not exercised
    NOT_REPRODUCIBLE = "NOT_REPRODUCIBLE"           # repeated and diverged
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"  # claim not backed
    EXPLORATORY_ONLY = "EXPLORATORY_ONLY"           # technically ok, not confirmatory


# Statuses that count as a clean positive result for gating purposes.
PASSING = {AgentStatus.PASS}
# Statuses that are honest non-results (block a biological conclusion but are not
# themselves a hard failure).
NON_RESULT = {AgentStatus.UNKNOWN, AgentStatus.NOT_TESTED,
              AgentStatus.INSUFFICIENT_EVIDENCE, AgentStatus.EXPLORATORY_ONLY}


@dataclass
class Verdict:
    agent: str
    status: AgentStatus
    summary: str
    findings: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)   # files/commands/records backing it
    confidence: str = "none"                             # none|low|medium|high

    def to_dict(self) -> dict[str, Any]:
        return {"agent": self.agent, "status": self.status.value, "summary": self.summary,
                "findings": self.findings, "evidence": self.evidence, "confidence": self.confidence}


@dataclass
class AgentContext:
    """Everything an agent needs to inspect a finished (or in-progress) run."""
    run_dir: Path
    bundles: list[dict[str, Any]] = field(default_factory=list)
    audit_records: list[dict[str, Any]] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    claims: list[dict[str, Any]] = field(default_factory=list)
    protected_roots: tuple[str, ...] = ()

    @classmethod
    def load(cls, run_dir: str | Path, *, claims_path: str | Path | None = None,
             protected_roots: tuple[str, ...] = ()) -> AgentContext:
        rd = Path(run_dir)
        bundles = []
        results = rd / "results"
        if results.is_dir():
            for f in sorted(results.glob("*.validation.json")):
                try:
                    bundles.append(json.loads(f.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        cfg = {}
        cfg_path = rd / "RUN_CONFIG.json"
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cfg = {}
        # audit records scoped to this run
        from .. import audit
        run_id = cfg.get("run_id", rd.name)
        recs = [r for r in audit.read()
                if (r.get("run_id") or r.get("harness_run_id")) == run_id
                or r.get("run_dir") == str(rd)]
        claims: list[dict[str, Any]] = []
        cp = Path(claims_path) if claims_path else (rd / "CLAIMS.json")
        if cp.exists():
            try:
                data = json.loads(cp.read_text(encoding="utf-8"))
                claims = data if isinstance(data, list) else data.get("claims", [])
            except (OSError, json.JSONDecodeError):
                claims = []
        return cls(run_dir=rd, bundles=bundles, audit_records=recs, config=cfg,
                   claims=claims, protected_roots=protected_roots)

    # -- convenience views over the bundles --------------------------------
    def succeeded_bundles(self) -> list[dict[str, Any]]:
        return [b for b in self.bundles if b.get("status_technical") == "SUCCEEDED"]

    def all_outputs(self) -> list[dict[str, Any]]:
        outs: list[dict[str, Any]] = []
        for b in self.bundles:
            outs.extend(b.get("outputs") or [])
        return outs


class Agent:
    """Base agent. Subclasses set ``name``/``gating`` and implement ``_check``."""
    name: str = "Agent"
    gating: bool = False          # whether a FAIL blocks biological conclusions

    def check(self, ctx: AgentContext) -> Verdict:
        try:
            return self._check(ctx)
        except Exception as exc:  # fault isolation: a broken agent -> honest UNKNOWN
            return Verdict(self.name, AgentStatus.UNKNOWN,
                           f"agent error (treated as unverified): {type(exc).__name__}: {exc}",
                           confidence="none")

    def _check(self, ctx: AgentContext) -> Verdict:  # pragma: no cover - abstract
        raise NotImplementedError
