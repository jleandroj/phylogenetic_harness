"""Multi-agent scientific verification layer.

A panel of single-responsibility agents inspects a finished run and the
CoordinatorAgent renders an honesty-first decision: a biological conclusion is
allowed only when the gating agents PASS with evidence — never on the absence of a
failure. The harness prefers UNKNOWN / NOT_TESTED / NOT_REPRODUCIBLE /
INSUFFICIENT_EVIDENCE / EXPLORATORY_ONLY over overstating.
"""
from __future__ import annotations

from .base import Agent, AgentContext, AgentStatus, Verdict
from .coordinator import (
    AGENT_ORDER,
    BIO_GATES,
    CoordinatorAgent,
    FinalStatus,
    verify_run,
)
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

__all__ = [
    "Agent", "AgentContext", "AgentStatus", "Verdict",
    "CoordinatorAgent", "FinalStatus", "AGENT_ORDER", "BIO_GATES", "verify_run",
    "InputIntegrityAgent", "ProvenanceAgent", "ReproducibilityAgent",
    "PhylogenyAgent", "AncestorValidationAgent", "FactGuardAgent",
    "StatisticsAgent", "BiologicalInterpretationAgent", "LiteratureAgent",
    "SecurityAgent", "ReportAgent", "RedTeamAuditorAgent",
]
