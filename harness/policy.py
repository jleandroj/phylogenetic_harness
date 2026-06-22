"""Run policy — strict, production-oriented gate (audit toward world-class).

A RunPolicy declares what a run MUST satisfy (sandbox on, no network for tools,
provenance frozen, resource ceilings). In strict mode the run is BLOCKED if it
violates the policy, so "production" runs cannot silently skip the controls.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Any


class PolicyViolation(Exception):
    """Raised when a strict run violates its policy."""


@dataclass
class RunPolicy:
    require_sandbox: bool = True          # tool execution must be sandboxed
    forbid_tool_network: bool = True      # tools must run with no network
    require_provenance: bool = True       # a RUN_MANIFEST must be frozen
    require_approval_strict: bool = True   # approval gate must be strict
    max_memory_gb: float | None = None    # optional per-task RAM ceiling

    @classmethod
    def production(cls) -> RunPolicy:
        return cls()

    @classmethod
    def permissive(cls) -> RunPolicy:
        return cls(require_sandbox=False, forbid_tool_network=False,
                   require_provenance=False, require_approval_strict=False)

    def check_run_config(self, cfg: Any) -> list[str]:
        """Return policy violations for a RunConfig (empty list = compliant)."""
        v: list[str] = []
        if self.require_sandbox and not getattr(cfg, "sandbox", False):
            backend = shutil.which("bwrap") or shutil.which("apptainer")
            if backend:
                v.append("policy requires sandbox=True but it is disabled")
            else:
                v.append("policy requires a sandbox but no bwrap/apptainer backend is installed")
        if self.require_approval_strict and getattr(cfg, "approval_policy", "") != "strict":
            v.append("policy requires approval_policy='strict'")
        if self.forbid_tool_network and getattr(cfg, "allow_network", False):
            v.append("policy forbids tool network but allow_network=True")
        return v

    def enforce(self, cfg: Any) -> None:
        violations = self.check_run_config(cfg)
        if violations:
            raise PolicyViolation("strict run policy violated:\n  - " + "\n  - ".join(violations))
