"""Science-layer agents: phylogeny, ancestor validation, fact-guard, statistics,
biological interpretation, literature/known-knowledge."""
from __future__ import annotations

import re
from pathlib import Path

from .base import Agent, AgentContext, AgentStatus, Verdict


class PhylogenyAgent(Agent):
    """Sanity-check produced trees: parseable Newick, ≥3 taxa, finite non-negative
    branch lengths. Does not bless a topology as biologically true — only that the
    tree object is structurally valid."""
    name = "PhylogenyAgent"
    gating = False

    def _check(self, ctx: AgentContext) -> Verdict:
        trees = [p for p in ctx.run_dir.rglob("*.nwk")] + [p for p in ctx.run_dir.rglob("*.treefile")]
        for o in ctx.all_outputs():
            op = Path(o.get("path", ""))
            if op.suffix.lower() in (".nwk", ".newick", ".tree") and op.exists():
                trees.append(op)
        trees = sorted(set(trees))
        if not trees:
            return Verdict(self.name, AgentStatus.NOT_TESTED, "no phylogenetic trees in this run")
        findings, bad = [], 0
        for t in trees:
            try:
                text = t.read_text(encoding="utf-8")
                ntaxa = text.count(",") + 1 if "(" in text else 0
                if "(" not in text or ";" not in text:
                    bad += 1
                    findings.append(f"{t.name}: not valid Newick")
                elif ntaxa < 3:
                    findings.append(f"{t.name}: only {ntaxa} taxa (degenerate)")
                if re.search(r":-\d", text):
                    bad += 1
                    findings.append(f"{t.name}: negative branch length")
            except OSError as exc:
                bad += 1
                findings.append(f"{t.name}: unreadable ({exc})")
        if bad:
            return Verdict(self.name, AgentStatus.FAIL, f"{bad} malformed tree(s)",
                           findings=findings, evidence=[str(t) for t in trees[:10]], confidence="high")
        return Verdict(self.name, AgentStatus.PASS, f"{len(trees)} tree(s) structurally valid",
                       findings=findings, evidence=[str(t) for t in trees[:10]], confidence="medium")


class AncestorValidationAgent(Agent):
    """A reconstructed ancestor is NOT an observed genome. It may never be a tree
    tip presented as evidence. Any reconstructed/non-reproducible taxon used as a
    phylogenetic observation is a hard FAIL (diagnostic-only at best)."""
    name = "AncestorValidationAgent"
    gating = True

    def _check(self, ctx: AgentContext) -> Verdict:
        from ..genome_phylo import is_reconstructed
        # candidate genome inputs across the run — including FASTAs consumed from
        # OUTSIDE the run dir (source genomes / reconstructed ancestors).
        fastas: list[Path] = []
        for ext in ("*.fa", "*.fasta", "*.fna"):
            fastas += list(ctx.run_dir.rglob(ext))
        for inp in ctx.input_files():
            p = Path(inp)
            if p.suffix.lower() in (".fa", ".fasta", ".fna"):
                fastas.append(p)
        fastas = sorted(set(fastas))
        reconstructed = [str(p) for p in fastas if p.exists() and is_reconstructed(p)]
        # did any bundle's observed-taxa-only check FAIL (ancestor used as a tip)?
        violated = []
        for b in ctx.bundles:
            for c in b.get("validation", []) or []:
                if c.get("name") == "observed_taxa_only" and c.get("status") == "FAILED":
                    violated.append(b.get("task_id"))
        if violated:
            return Verdict(self.name, AgentStatus.FAIL,
                           "reconstructed ancestor(s) used as observed taxa in a tree",
                           findings=[f"task {t}: observed_taxa_only FAILED" for t in violated],
                           evidence=reconstructed[:10], confidence="high")
        if reconstructed:
            return Verdict(self.name, AgentStatus.EXPLORATORY_ONLY,
                           f"{len(reconstructed)} reconstructed genome(s) present — "
                           "diagnostic only, not observed evidence",
                           findings=reconstructed[:10], evidence=reconstructed[:10],
                           confidence="medium")
        return Verdict(self.name, AgentStatus.NOT_TESTED,
                       "no reconstructed ancestors detected in this run")


# Overclaim words that must not appear in a report without backing evidence.
_OVERCLAIM = re.compile(r"(?i)\b(prov(e|es|en|ed)|confirm(s|ed)?|definitive(ly)?|"
                        r"100% ?(certain|sure)|novel|first to show|unprecedented|"
                        r"clearly demonstrates?)\b")


class FactGuardAgent(Agent):
    """Every scientific claim must cite evidence: a file, command, audit record,
    paper, database entry, or reproducible result. Claims without evidence FAIL.
    Report prose is scanned for unbacked overclaims."""
    name = "FactGuardAgent"
    gating = True

    def _check(self, ctx: AgentContext) -> Verdict:
        unbacked = []
        for c in ctx.claims:
            stmt = c.get("statement") or c.get("claim") or "<unnamed>"
            ev = c.get("evidence") or []
            if not ev:
                unbacked.append(stmt)
        # scan report-like text for overclaims
        overclaims = []
        for name in ("REPORT.md", "README.md", "RUN_SUMMARY.json", "report.md"):
            p = ctx.run_dir / name
            if p.exists():
                try:
                    for m in set(_OVERCLAIM.findall(p.read_text(encoding="utf-8", errors="replace"))):
                        token = m[0] if isinstance(m, tuple) else m
                        overclaims.append(f"{name}: '{token}'")
                except OSError:
                    pass
        if unbacked:
            return Verdict(self.name, AgentStatus.FAIL,
                           f"{len(unbacked)} scientific claim(s) without evidence",
                           findings=[f"UNBACKED: {s}" for s in unbacked] +
                                    [f"OVERCLAIM: {o}" for o in overclaims],
                           confidence="high")
        if not ctx.claims:
            # No explicit claims: prose overclaims are flagged (INSUFFICIENT_EVIDENCE)
            # but don't hard-FAIL a run that makes no scientific assertion.
            status = AgentStatus.INSUFFICIENT_EVIDENCE if overclaims else AgentStatus.NOT_TESTED
            return Verdict(self.name, status,
                           "report uses strong language with no claim/evidence file" if overclaims
                           else "no explicit claims to guard (provide CLAIMS.json)",
                           findings=[f"OVERCLAIM: {o}" for o in overclaims], confidence="medium")
        return Verdict(self.name, AgentStatus.PASS,
                       f"all {len(ctx.claims)} claim(s) cite evidence",
                       findings=[f"OVERCLAIM: {o}" for o in overclaims],
                       evidence=[c.get("statement", "") for c in ctx.claims][:10],
                       confidence="high" if not overclaims else "low")


class StatisticsAgent(Agent):
    """Guard statistical claims: p-values need a multiple-testing correction, a
    declared n, and a model. Absent any stats, NOT_TESTED (honest)."""
    name = "StatisticsAgent"
    gating = False

    def _check(self, ctx: AgentContext) -> Verdict:
        stats = []
        for c in ctx.claims:
            s = c.get("statistics") or {}
            if s or c.get("p_value") is not None:
                stats.append({**s, "p_value": c.get("p_value", s.get("p_value")),
                              "statement": c.get("statement")})
        if not stats:
            return Verdict(self.name, AgentStatus.NOT_TESTED, "no statistical claims in this run")
        problems = []
        for s in stats:
            if s.get("p_value") is not None:
                if not s.get("multiple_testing_correction"):
                    problems.append(f"{s.get('statement')}: p-value without multiple-testing correction")
                if not s.get("n"):
                    problems.append(f"{s.get('statement')}: p-value without sample size n")
        if problems:
            return Verdict(self.name, AgentStatus.FAIL, f"{len(problems)} statistical issue(s)",
                           findings=problems, confidence="high")
        return Verdict(self.name, AgentStatus.PASS,
                       f"{len(stats)} statistical claim(s) declare correction + n + model",
                       confidence="medium")


class BiologicalInterpretationAgent(Agent):
    """Translate technical results to biology — but only when the run's own science
    layer says it is interpretable. Degenerate / not-evaluated / low-confidence
    results are reported as biologically UNSUPPORTED, not interpreted."""
    name = "BiologicalInterpretationAgent"
    gating = False

    _INTERPRETABLE = {"BIOLOGICALLY_INTERPRETABLE", "SUPPORTED", "NEGATIVE_RESULT"}
    _UNSUPPORTED = {"DEGENERATE", "NOT_EVALUATED", "NOT_BIOLOGICALLY_INTERPRETABLE",
                    "LOW_CONFIDENCE", "INCONCLUSIVE", "INPUT_LIMITED", "MODEL_LIMITED"}

    def _check(self, ctx: AgentContext) -> Verdict:
        succeeded = ctx.succeeded_bundles()
        if not succeeded:
            return Verdict(self.name, AgentStatus.NOT_TESTED, "no successful task to interpret")
        interpretable, unsupported = [], []
        for b in succeeded:
            sci = b.get("status_scientific")
            tid = b.get("task_id")
            if b.get("degenerate"):
                unsupported.append(f"{tid}: DEGENERATE output")
            elif sci in self._INTERPRETABLE:
                interpretable.append(f"{tid}: {sci}")
            elif sci in self._UNSUPPORTED:
                unsupported.append(f"{tid}: {sci}")
        if interpretable and not unsupported:
            return Verdict(self.name, AgentStatus.PASS,
                           f"{len(interpretable)} result(s) biologically interpretable",
                           findings=interpretable, confidence="medium")
        if interpretable:
            return Verdict(self.name, AgentStatus.EXPLORATORY_ONLY,
                           "mixed: some interpretable, some unsupported",
                           findings=interpretable + unsupported, confidence="low")
        return Verdict(self.name, AgentStatus.INSUFFICIENT_EVIDENCE,
                       "no result is biologically interpretable (degenerate/uncertain)",
                       findings=unsupported, confidence="low")


class LiteratureAgent(Agent):
    """Check whether a finding is already known. Without a real literature source
    (network egress is sandboxed off), novelty cannot be asserted — so 'novel'
    claims are downgraded to UNKNOWN rather than invented. A local known-knowledge
    file, if provided, is consulted."""
    name = "LiteratureAgent"
    gating = False

    def _check(self, ctx: AgentContext) -> Verdict:
        novelty_claims = [c.get("statement", "") for c in ctx.claims
                          if c.get("novel") or re.search(r"(?i)\bnovel\b", c.get("statement", ""))]
        known_db = ctx.run_dir / "known_knowledge.json"
        if not novelty_claims:
            return Verdict(self.name, AgentStatus.NOT_TESTED, "no novelty claims to check")
        if not known_db.exists():
            return Verdict(self.name, AgentStatus.UNKNOWN,
                           "novelty asserted but no literature source available "
                           "(network is sandboxed; provide known_knowledge.json) — cannot call it novel",
                           findings=novelty_claims, confidence="none")
        # with a local DB we can at least flag overlaps
        import json
        try:
            known = set(json.loads(known_db.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            known = set()
        overlap = [c for c in novelty_claims if any(k.lower() in c.lower() for k in known)]
        if overlap:
            return Verdict(self.name, AgentStatus.FAIL,
                           "claim marked novel overlaps known knowledge",
                           findings=overlap, evidence=[str(known_db)], confidence="medium")
        return Verdict(self.name, AgentStatus.PASS,
                       "novelty claims not found in the provided known-knowledge set",
                       evidence=[str(known_db)], confidence="low")
