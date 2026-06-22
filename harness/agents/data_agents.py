"""Data-layer agents: input integrity, provenance, reproducibility."""
from __future__ import annotations

from pathlib import Path

from .base import Agent, AgentContext, AgentStatus, Verdict

# Minimal format signatures so a mislabeled/garbage input is caught early.
_FORMAT_SIGNATURE = {
    ".fa": ">", ".fasta": ">", ".fna": ">", ".faa": ">",
    ".vcf": "##", ".gtf": None, ".gff": None, ".gff3": None,
    ".nwk": "(", ".newick": "(", ".tree": "(",
}


class InputIntegrityAgent(Agent):
    """Validate every declared input: existence, basic format signature, and —
    when a baseline manifest exists — that the bytes have not changed."""
    name = "InputIntegrityAgent"
    gating = True

    def _check(self, ctx: AgentContext) -> Verdict:
        from .. import integrity
        inputs: list[str] = []
        for b in ctx.bundles:
            for tid_in in (b.get("execution") or {}).get("inputs", []) or []:
                inputs.append(tid_in)
        # bundles also carry inputs_sha256 keys
        for b in ctx.bundles:
            inputs.extend((b.get("inputs_sha256") or {}).keys())
        inputs = sorted(set(inputs))
        if not inputs:
            return Verdict(self.name, AgentStatus.NOT_TESTED, "no declared inputs to validate")

        findings, missing, badfmt = [], [], []
        for inp in inputs:
            p = Path(inp)
            if not p.exists():
                missing.append(inp)
                continue
            if p.is_file():
                sig = _FORMAT_SIGNATURE.get(p.suffix.lower(), "__none__")
                if isinstance(sig, str) and sig != "__none__":
                    try:
                        head = p.read_text(encoding="utf-8", errors="replace").lstrip()[:4]
                        if not head.startswith(sig):
                            badfmt.append(f"{inp} (expected to start with {sig!r})")
                    except OSError:
                        pass
        # baseline check via recorded inputs_sha256 vs current bytes
        changed = []
        for b in ctx.bundles:
            base = {k: v for k, v in (b.get("inputs_sha256") or {}).items() if v}
            for v in integrity.verify_inputs(list(base), base):
                changed.append(v)

        if missing or changed:
            findings += [f"MISSING: {m}" for m in missing] + [f"CHANGED: {c}" for c in changed]
            return Verdict(self.name, AgentStatus.FAIL,
                           f"{len(missing)} missing, {len(changed)} changed input(s)",
                           findings=findings, evidence=inputs[:20], confidence="high")
        if badfmt:
            return Verdict(self.name, AgentStatus.FAIL,
                           f"{len(badfmt)} input(s) fail format signature",
                           findings=badfmt, evidence=inputs[:20], confidence="medium")
        return Verdict(self.name, AgentStatus.PASS,
                       f"all {len(inputs)} input(s) present, well-formed, unchanged",
                       evidence=inputs[:20], confidence="high")


class ProvenanceAgent(Agent):
    """Every executed action must be on the tamper-evident audit chain with a full
    record (argv, exit, duration), and the chain must verify."""
    name = "ProvenanceAgent"
    gating = True

    def _check(self, ctx: AgentContext) -> Verdict:
        from .. import audit
        chain = audit.verify()
        if not chain["ok"]:
            return Verdict(self.name, AgentStatus.FAIL,
                           f"audit chain broken: {chain.get('reason')} at {chain.get('broken_at')}",
                           findings=[chain.get("reason", "")], confidence="high")
        finished = [r for r in ctx.audit_records if r.get("event") == "action_finished"]
        executed = [b for b in ctx.bundles if (b.get("execution") or {}).get("exit_code") is not None]
        if not ctx.audit_records:
            return Verdict(self.name, AgentStatus.UNKNOWN,
                           "no audit records found for this run", confidence="none")
        missing_record = []
        for b in executed:
            tid = b.get("task_id")
            if not any(r.get("task_id") == tid for r in finished):
                missing_record.append(tid)
        manifest_present = (ctx.run_dir / "RUN_MANIFEST.json").exists()
        ev = [f"audit_records={len(ctx.audit_records)}", f"action_finished={len(finished)}",
              f"chain_keyed={chain.get('keyed')}", f"manifest={manifest_present}"]
        if missing_record:
            return Verdict(self.name, AgentStatus.FAIL,
                           f"{len(missing_record)} executed task(s) lack a full action record",
                           findings=[str(t) for t in missing_record], evidence=ev, confidence="high")
        status = AgentStatus.PASS if (finished or not executed) else AgentStatus.UNKNOWN
        return Verdict(self.name, status,
                       "audit chain verifies and every action is fully recorded",
                       evidence=ev, confidence="high" if status == AgentStatus.PASS else "low")


class ReproducibilityAgent(Agent):
    """A result is only reproducible if it was actually repeated and matched. If it
    was never repeated, say NOT_TESTED — never assume reproducibility. Reconstructed
    / non-deterministic artefacts are marked NOT_REPRODUCIBLE."""
    name = "ReproducibilityAgent"
    gating = True

    def _check(self, ctx: AgentContext) -> Verdict:
        from .. import diff
        # 1) explicit non-determinism markers anywhere in the run workspace
        nd = list(ctx.run_dir.rglob("NON_DETERMINISTIC_WARNING.txt"))
        if nd:
            return Verdict(self.name, AgentStatus.NOT_REPRODUCIBLE,
                           "run contains non-determinism warnings (reconstructed/unstable artefacts)",
                           findings=[str(p) for p in nd[:10]],
                           evidence=[str(p) for p in nd[:10]], confidence="high")
        # 2) was a replay/diff actually performed? look for a sibling replay run
        replay_dir = ctx.run_dir.parent / (ctx.run_dir.name + "_replay")
        if replay_dir.exists():
            try:
                report = diff.diff_runs(ctx.run_dir, replay_dir)
                drift = report.get("result_drift") or report.get("drift")
                if drift:
                    return Verdict(self.name, AgentStatus.NOT_REPRODUCIBLE,
                                   "replay diverged from original", findings=[str(drift)],
                                   evidence=[str(replay_dir)], confidence="high")
                return Verdict(self.name, AgentStatus.PASS,
                               "replay reproduced the original byte-for-byte",
                               evidence=[str(replay_dir)], confidence="high")
            except Exception as exc:  # noqa: BLE001
                return Verdict(self.name, AgentStatus.UNKNOWN,
                               f"replay present but diff failed: {exc}", confidence="low")
        return Verdict(self.name, AgentStatus.NOT_TESTED,
                       "critical results were not repeated; reproducibility unverified "
                       "(run `harness replay`/a second run to confirm)", confidence="none")
