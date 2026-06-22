"""CoordinatorAgent — orchestrates the verification agents and renders the final,
honesty-first decision.

Hard rule: a biological conclusion (PASS) is allowed ONLY when the gating agents
are explicitly PASS — never on the mere absence of a failure. Anything less is
downgraded to PASS_EXPLORATORY or UNKNOWN, and any breach maps to a specific FAIL.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from .base import AgentContext, AgentStatus, Verdict
from .data_agents import InputIntegrityAgent, ProvenanceAgent, ReproducibilityAgent
from .ops_agents import RedTeamAuditorAgent, ReportAgent, SecurityAgent
from .science_agents import (
    AncestorValidationAgent,
    BiologicalInterpretationAgent,
    FactGuardAgent,
    LiteratureAgent,
    PhylogenyAgent,
    StatisticsAgent,
)


class FinalStatus(str, Enum):
    PASS = "PASS"
    PASS_EXPLORATORY = "PASS_EXPLORATORY"
    FAIL_TECHNICAL = "FAIL_TECHNICAL"
    FAIL_REPRODUCIBILITY = "FAIL_REPRODUCIBILITY"
    FAIL_EVIDENCE = "FAIL_EVIDENCE"
    FAIL_SECURITY = "FAIL_SECURITY"
    UNKNOWN = "UNKNOWN"


# Run order: security & data integrity first, then domain, then evidence/biology,
# then the adversarial red-team last (it attacks whatever the rest concluded).
AGENT_ORDER = [
    SecurityAgent, InputIntegrityAgent, ProvenanceAgent, ReproducibilityAgent,
    PhylogenyAgent, AncestorValidationAgent, StatisticsAgent,
    FactGuardAgent, BiologicalInterpretationAgent, LiteratureAgent,
    RedTeamAuditorAgent,
]

# Agents that must be explicitly PASS for a biological conclusion (PASS).
BIO_GATES = ("ProvenanceAgent", "ReproducibilityAgent", "FactGuardAgent",
             "AncestorValidationAgent", "BiologicalInterpretationAgent")


class CoordinatorAgent:
    def __init__(self, agents: list | None = None) -> None:
        self.agents = [a() for a in (agents or AGENT_ORDER)]

    def verify(self, ctx: AgentContext) -> dict[str, Any]:
        verdicts: list[Verdict] = []
        for agent in self.agents:
            verdicts.append(agent.check(ctx))
        status, rationale = self._decide(verdicts)
        decision = {
            "run_id": ctx.config.get("run_id", ctx.run_dir.name),
            "run_dir": str(ctx.run_dir),
            "status": status.value,
            "rationale": rationale,
            "allow_biological_conclusion": status == FinalStatus.PASS,
            "verdicts": [v.to_dict() for v in verdicts],
        }
        try:
            ReportAgent.write(ctx, decision)
        except OSError:
            pass
        # record the verdict to the central audit log (best-effort)
        try:
            from .. import audit
            audit.record("verification_decision", run_dir=str(ctx.run_dir),
                         status=status.value)
        except Exception:  # noqa: BLE001
            pass
        return decision

    @staticmethod
    def _decide(verdicts: list[Verdict]) -> tuple[FinalStatus, str]:
        g = {v.agent: v.status for v in verdicts}

        # 1) hard failures, in precedence order
        if g.get("SecurityAgent") == AgentStatus.FAIL:
            return FinalStatus.FAIL_SECURITY, "Containment/integrity breach detected by SecurityAgent."
        if g.get("RedTeamAuditorAgent") == AgentStatus.FAIL:
            return (FinalStatus.FAIL_EVIDENCE,
                    "Red-team found masked failures or recycled/degenerate outputs sold as success.")
        if AgentStatus.FAIL in (g.get("InputIntegrityAgent"), g.get("ProvenanceAgent")):
            return FinalStatus.FAIL_TECHNICAL, "Inputs or provenance failed validation."
        if g.get("ReproducibilityAgent") in (AgentStatus.FAIL, AgentStatus.NOT_REPRODUCIBLE):
            return (FinalStatus.FAIL_REPRODUCIBILITY,
                    "Critical results did not reproduce / are flagged non-reproducible.")
        if AgentStatus.FAIL in (g.get("FactGuardAgent"), g.get("AncestorValidationAgent")):
            return (FinalStatus.FAIL_EVIDENCE,
                    "A scientific claim lacked evidence, or a reconstructed ancestor was "
                    "treated as observed.")

        # 2) biological conclusion allowed only if EVERY bio-gate is explicitly PASS
        if all(g.get(a) == AgentStatus.PASS for a in BIO_GATES):
            return (FinalStatus.PASS,
                    "All gating agents PASS with evidence; biological conclusion supported.")

        # 3) technically clean but not enough for a biological conclusion
        tech_ok = (g.get("InputIntegrityAgent") in (AgentStatus.PASS, AgentStatus.NOT_TESTED)
                   and g.get("ProvenanceAgent") in (AgentStatus.PASS, AgentStatus.UNKNOWN)
                   and g.get("SecurityAgent") in (AgentStatus.PASS, AgentStatus.NOT_TESTED))
        if tech_ok:
            blockers = [a for a in BIO_GATES if g.get(a) != AgentStatus.PASS]
            return (FinalStatus.PASS_EXPLORATORY,
                    "Technically valid but NOT a confirmed biological result; unmet bio-gates: "
                    + ", ".join(f"{a}={st.value if (st := g.get(a)) else 'NONE'}" for a in blockers))

        # 4) cannot tell
        return (FinalStatus.UNKNOWN,
                "Insufficient verification to reach any conclusion (missing audit/inputs/results).")


def verify_run(run_dir: str | Path, *, claims_path: str | Path | None = None,
               protected_roots: tuple[str, ...] = ()) -> dict[str, Any]:
    """Convenience entry point: load context + run the coordinator."""
    ctx = AgentContext.load(run_dir, claims_path=claims_path, protected_roots=protected_roots)
    return CoordinatorAgent().verify(ctx)
