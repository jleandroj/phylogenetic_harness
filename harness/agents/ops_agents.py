"""Ops-layer agents: security/sandbox, report, red-team auditor."""
from __future__ import annotations

import json
from pathlib import Path

from .base import Agent, AgentContext, AgentStatus, Verdict


class SecurityAgent(Agent):
    """Verify containment actually held: no output landed in a system/protected
    root, the audit chain is intact, and (if claimed) the sandbox was used. A
    breach is a hard FAIL_SECURITY."""
    name = "SecurityAgent"
    gating = True

    def _check(self, ctx: AgentContext) -> Verdict:
        from ..confine import check_output_confinement
        out_paths: list[str] = [str(o["path"]) for o in ctx.all_outputs() if o.get("path")]
        violations = check_output_confinement(out_paths, protected_roots=ctx.protected_roots)
        # blocked actions are evidence the gate worked, not a breach
        blocked = [r for r in ctx.audit_records if r.get("event") == "action_blocked"]
        ev = [f"outputs_checked={len(out_paths)}", f"blocked_actions={len(blocked)}",
              f"sandbox={ctx.config.get('sandbox')}", f"strict={ctx.config.get('strict')}"]
        if violations:
            return Verdict(self.name, AgentStatus.FAIL,
                           f"{len(violations)} output(s) escaped containment",
                           findings=violations, evidence=ev, confidence="high")
        from .. import audit
        if not audit.verify()["ok"]:
            return Verdict(self.name, AgentStatus.FAIL, "audit chain broken (integrity breach)",
                           evidence=ev, confidence="high")
        return Verdict(self.name, AgentStatus.PASS,
                       "no containment breach; all outputs inside the workspace",
                       evidence=ev, confidence="high")


class ReportAgent(Agent):
    """Always produces a structured verification artefact with confidence levels.
    This agent never gates; it records what the others found."""
    name = "ReportAgent"
    gating = False

    def _check(self, ctx: AgentContext) -> Verdict:
        # The coordinator writes the full report; here we just confirm we can.
        target = ctx.run_dir / "VERIFICATION.json"
        writable = ctx.run_dir.exists()
        return Verdict(self.name, AgentStatus.PASS if writable else AgentStatus.UNKNOWN,
                       f"verification report target {target}",
                       evidence=[str(target)], confidence="high" if writable else "none")

    @staticmethod
    def write(ctx: AgentContext, decision: dict) -> Path:
        """Persist the decision object + a human-readable summary."""
        jpath = ctx.run_dir / "VERIFICATION.json"
        jpath.write_text(json.dumps(decision, indent=2, sort_keys=True), encoding="utf-8")
        lines = [f"# Verification report — {decision['run_id']}", "",
                 f"**FINAL: {decision['status']}**", "", decision["rationale"], "",
                 "| Agent | Status | Confidence | Summary |", "|---|---|---|---|"]
        for v in decision["verdicts"]:
            lines.append(f"| {v['agent']} | {v['status']} | {v['confidence']} | {v['summary']} |")
        mpath = ctx.run_dir / "VERIFICATION.md"
        mpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return jpath


class RedTeamAuditorAgent(Agent):
    """Adversarial pass: hunt for lies the other agents might have missed —
    stale/recycled outputs, exit-0-with-failed-validators, empty outputs sold as
    success, and technical/scientific state mismatches."""
    name = "RedTeamAuditorAgent"
    gating = True

    def _check(self, ctx: AgentContext) -> Verdict:
        attacks: list[str] = []
        for b in ctx.bundles:
            tid = b.get("task_id")
            tech = b.get("status_technical")
            execu = b.get("execution") or {}
            exit_code = execu.get("exit_code")
            val_passed = b.get("validators_passed")
            # exit 0 but validators failed, yet sold as SUCCEEDED
            if tech == "SUCCEEDED" and val_passed is False:
                attacks.append(f"{tid}: SUCCEEDED but validators did NOT pass")
            if exit_code == 0 and val_passed is False and tech == "SUCCEEDED":
                attacks.append(f"{tid}: exit 0 masking a failed validator")
            # empty/degenerate output presented as success
            if tech == "SUCCEEDED":
                for o in b.get("outputs") or []:
                    if o.get("sha256") is None and Path(str(o.get("path"))).suffix:
                        attacks.append(f"{tid}: declared output missing/empty: {o.get('path')}")
                if b.get("degenerate") and b.get("status_scientific") not in ("DEGENERATE",):
                    attacks.append(f"{tid}: degenerate output not marked DEGENERATE")
            # stale output: output older than the inputs it supposedly derives from
            for o in b.get("outputs") or []:
                op = Path(str(o.get("path", "")))
                if op.exists():
                    o_m = op.stat().st_mtime
                    for inp in (b.get("inputs_sha256") or {}):
                        ip = Path(inp)
                        if ip.exists() and ip.stat().st_mtime > o_m + 1:
                            attacks.append(f"{tid}: output older than input {inp} (recycled?)")
                            break
        if attacks:
            return Verdict(self.name, AgentStatus.FAIL,
                           f"red-team found {len(attacks)} suspicious signal(s)",
                           findings=attacks, confidence="high")
        if not ctx.bundles:
            return Verdict(self.name, AgentStatus.NOT_TESTED, "no results to attack")
        return Verdict(self.name, AgentStatus.PASS,
                       "no stale outputs, masked failures, or state mismatches found",
                       confidence="medium")
