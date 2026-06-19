"""Scientific interpretation layer (spec §9–§14, §24.5–§24.8).

This module is where the harness refuses to let "it ran" become "it is true".
It produces a three-level verdict (technical / statistical / biological), with
explicit lists of what may and may not be concluded, plus a confidence level.

It also classifies negative results into the spec's categories and detects
degenerate outputs, so a negative or empty result is never silently recorded as
either success or failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from .states import ScientificState
from .validators import CheckResult


# ---- negative-result classification (spec §24.7) -----------------------------

class NegativeCategory(str, Enum):
    TRUE_NEGATIVE_POSSIBLE = "TRUE_NEGATIVE_POSSIBLE"
    LOW_POWER = "LOW_POWER"
    BAD_INPUT = "BAD_INPUT"
    MODEL_MISMATCH = "MODEL_MISMATCH"
    INSUFFICIENT_SIGNAL = "INSUFFICIENT_SIGNAL"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class NegativeResult:
    observed: bool
    category: NegativeCategory
    technical_success: bool
    reason: str
    biological_interpretation: str = "inconclusive"

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed": self.observed,
            "category": self.category.value,
            "technical_success": self.technical_success,
            "biological_interpretation": self.biological_interpretation,
            "reason": self.reason,
        }


def classify_negative(
    *,
    technical_success: bool,
    input_quality_ok: bool,
    has_signal: bool,
    support_above_threshold: bool,
    model_appropriate: bool,
    conflicting: bool,
    reason: str = "",
) -> NegativeResult:
    """Map evidence flags to a negative-result category (spec §24.7).

    A negative result is NEVER auto-marked as failure: even with a technical
    failure we record the category so the cause is explicit.
    """
    if not technical_success:
        cat = NegativeCategory.TECHNICAL_FAILURE
    elif not input_quality_ok:
        cat = NegativeCategory.BAD_INPUT
    elif not model_appropriate:
        cat = NegativeCategory.MODEL_MISMATCH
    elif conflicting:
        cat = NegativeCategory.CONFLICTING_EVIDENCE
    elif not has_signal:
        cat = NegativeCategory.INSUFFICIENT_SIGNAL
    elif not support_above_threshold:
        cat = NegativeCategory.LOW_POWER
    else:
        # Everything technically fine and signal present, yet result is negative:
        # a genuine negative is possible.
        cat = NegativeCategory.TRUE_NEGATIVE_POSSIBLE
    return NegativeResult(
        observed=True,
        category=cat,
        technical_success=technical_success,
        reason=reason or f"classified as {cat.value}",
    )


# ---- degenerate-output detection (spec §24.8) --------------------------------

@dataclass
class DegeneracyReport:
    degenerate: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"degenerate": self.degenerate, "reasons": list(self.reasons)}


def detect_degenerate(
    *,
    output_size_bytes: int | None = None,
    n_records: int | None = None,
    n_records_expected_positive: bool = True,
    metrics: Sequence[float] | None = None,
    all_hashes: Sequence[str] | None = None,
    hashes_expected_distinct: bool = False,
    only_gaps_or_n: bool | None = None,
    placeholder_detected: bool = False,
) -> DegeneracyReport:
    """Flag suspicious outputs that passed technical validation (spec §24.8)."""
    reasons: list[str] = []
    if output_size_bytes is not None and output_size_bytes == 0:
        reasons.append("output is empty")
    if n_records is not None and n_records == 0 and n_records_expected_positive:
        reasons.append("zero records where a positive count was expected")
    if metrics:
        uniq = set(round(m, 12) for m in metrics)
        if len(uniq) == 1 and len(metrics) > 1:
            val = next(iter(uniq))
            reasons.append(f"metric is constant across all items ({val})")
        if all(m == 0.0 for m in metrics) and len(metrics) > 1:
            reasons.append("all metrics are exactly 0.0")
        if all(m == 1.0 for m in metrics) and len(metrics) > 1:
            reasons.append("all metrics are exactly 1.0")
    if all_hashes and hashes_expected_distinct:
        if len(set(all_hashes)) == 1 and len(all_hashes) > 1:
            reasons.append("all outputs share one hash though they should differ")
    if only_gaps_or_n:
        reasons.append("output is only gaps/N in critical regions")
    if placeholder_detected:
        reasons.append("placeholder value detected")
    # dedupe preserving order
    seen: set[str] = set()
    deduped = [r for r in reasons if not (r in seen or seen.add(r))]
    return DegeneracyReport(degenerate=bool(deduped), reasons=deduped)


# ---- three-level interpretation (spec §24.5/§24.6) ---------------------------

@dataclass
class LevelVerdict:
    status: str  # PASSED | FAILED | LIMITED | NOT_APPLICABLE
    checks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "checks": self.checks}


@dataclass
class Interpretation:
    technical: LevelVerdict
    statistical: LevelVerdict
    biological: LevelVerdict
    scientific_state: ScientificState
    interpretation_allowed: list[str] = field(default_factory=list)
    interpretation_not_allowed: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    confidence: str = "not_evaluated"  # high | medium | low | not_evaluated

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation": {
                "technical": self.technical.to_dict(),
                "statistical": self.statistical.to_dict(),
                "biological": self.biological.to_dict(),
            },
            "scientific_state": self.scientific_state.value,
            "interpretation_allowed": list(self.interpretation_allowed),
            "interpretation_not_allowed": list(self.interpretation_not_allowed),
            "limitations": list(self.limitations),
            "confidence": self.confidence,
        }


# Standing prohibitions that apply to phylogenetic claims regardless of how
# clean the computation looked (spec §13/§14). Always surfaced so no report can
# silently overclaim.
DEFAULT_NOT_ALLOWED = [
    "This result does not prove a single 'true' absolute tree.",
    "A local tree is not necessarily identical to the species tree (ILS/recombination).",
    "An inferred ancestral sequence is a model estimate, not a real individual genome.",
    "Technical success (exit code 0, valid format) does not imply biological correctness.",
]


def build_interpretation(
    technical_checks: Sequence[CheckResult],
    *,
    statistical_checks: Sequence[CheckResult] | None = None,
    degeneracy: DegeneracyReport | None = None,
    negative: NegativeResult | None = None,
    allowed: Sequence[str] | None = None,
    extra_not_allowed: Sequence[str] | None = None,
    limitations: Sequence[str] | None = None,
) -> Interpretation:
    """Assemble a full interpretation from validator results and evidence flags.

    The biological level NEVER passes off the back of technical checks alone: it
    is at most LIMITED unless explicit biological evidence is supplied via the
    statistical level and the absence of degeneracy/negativity.
    """
    tech_status = "PASSED" if all(c.passed for c in technical_checks) else "FAILED"
    technical = LevelVerdict(tech_status, [c.to_dict() for c in technical_checks])

    if statistical_checks:
        stat_status = "PASSED" if all(c.passed for c in statistical_checks) else "FAILED"
        statistical = LevelVerdict(stat_status, [c.to_dict() for c in statistical_checks])
    else:
        statistical = LevelVerdict("NOT_APPLICABLE", [])

    not_allowed = list(DEFAULT_NOT_ALLOWED)
    if extra_not_allowed:
        not_allowed.extend(extra_not_allowed)

    # Decide scientific state + biological level + confidence.
    if degeneracy and degeneracy.degenerate:
        sci = ScientificState.DEGENERATE
        biological = LevelVerdict("FAILED", [{"name": "degenerate", "status": "FAILED",
                                              "detail": "; ".join(degeneracy.reasons)}])
        confidence = "low"
    elif tech_status == "FAILED":
        sci = ScientificState.NOT_BIOLOGICALLY_INTERPRETABLE
        biological = LevelVerdict("NOT_APPLICABLE", [])
        confidence = "not_evaluated"
    elif negative and negative.observed:
        mapping = {
            NegativeCategory.TRUE_NEGATIVE_POSSIBLE: ScientificState.NEGATIVE_RESULT,
            NegativeCategory.CONFLICTING_EVIDENCE: ScientificState.CONFLICTING_EVIDENCE,
            NegativeCategory.MODEL_MISMATCH: ScientificState.MODEL_LIMITED,
            NegativeCategory.BAD_INPUT: ScientificState.INPUT_LIMITED,
            NegativeCategory.LOW_POWER: ScientificState.LOW_CONFIDENCE,
            NegativeCategory.INSUFFICIENT_SIGNAL: ScientificState.INCONCLUSIVE,
            NegativeCategory.INCONCLUSIVE: ScientificState.INCONCLUSIVE,
            NegativeCategory.TECHNICAL_FAILURE: ScientificState.NOT_BIOLOGICALLY_INTERPRETABLE,
        }
        sci = mapping.get(negative.category, ScientificState.INCONCLUSIVE)
        biological = LevelVerdict("LIMITED", [{"name": "negative_result", "status": "LIMITED",
                                              "detail": negative.category.value}])
        confidence = "low"
    elif statistical.status == "PASSED":
        # Technical + statistical support present: interpretable, but biology
        # still bounded by standing prohibitions.
        sci = ScientificState.BIOLOGICALLY_INTERPRETABLE
        biological = LevelVerdict("PASSED", [])
        confidence = "medium"
    else:
        # Technically valid only: explicitly LIMITED biological interpretation.
        sci = ScientificState.LOW_CONFIDENCE
        biological = LevelVerdict("LIMITED", [{"name": "technical_only", "status": "LIMITED",
                                              "detail": "passed technical validation only"}])
        confidence = "low"

    return Interpretation(
        technical=technical,
        statistical=statistical,
        biological=biological,
        scientific_state=sci,
        interpretation_allowed=list(allowed or []),
        interpretation_not_allowed=not_allowed,
        limitations=list(limitations or []),
        confidence=confidence,
    )
