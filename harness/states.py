"""Task state machines (spec §9, §24.3).

The central invariant of the whole harness: *technical* state and *scientific*
state are SEPARATE. A task that SUCCEEDED technically says nothing about whether
its result is biologically SUPPORTED. The two enums never share members and the
code never maps one onto the other automatically.

    SUCCEEDED  does NOT imply  SUPPORTED
    FAILED     does NOT imply  the hypothesis is false
    NEGATIVE   does NOT imply  a technical failure
    VALID_FORMAT does NOT imply the result is correct
"""
from __future__ import annotations

from enum import Enum


class IllegalTransition(Exception):
    """Raised when an undeclared state transition is attempted."""


class TechnicalState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FATAL = "FAILED_FATAL"
    REQUEUED = "REQUEUED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ScientificState(str, Enum):
    NOT_EVALUATED = "NOT_EVALUATED"
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    NEGATIVE_RESULT = "NEGATIVE_RESULT"
    INCONCLUSIVE = "INCONCLUSIVE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    DEGENERATE = "DEGENERATE"
    MODEL_LIMITED = "MODEL_LIMITED"
    INPUT_LIMITED = "INPUT_LIMITED"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    BIOLOGICALLY_INTERPRETABLE = "BIOLOGICALLY_INTERPRETABLE"
    NOT_BIOLOGICALLY_INTERPRETABLE = "NOT_BIOLOGICALLY_INTERPRETABLE"


# Declared legal technical transitions. Anything not listed raises.
_TECH_TRANSITIONS: dict[TechnicalState, set[TechnicalState]] = {
    TechnicalState.PENDING: {
        TechnicalState.APPROVED,
        TechnicalState.CANCELLED,
    },
    TechnicalState.APPROVED: {
        TechnicalState.LEASED,
        TechnicalState.CANCELLED,
    },
    TechnicalState.LEASED: {
        TechnicalState.RUNNING,
        TechnicalState.EXPIRED,
        TechnicalState.CANCELLED,
    },
    TechnicalState.RUNNING: {
        TechnicalState.SUCCEEDED,
        TechnicalState.FAILED_RETRYABLE,
        TechnicalState.FAILED_FATAL,
        TechnicalState.EXPIRED,  # worker/lease died mid-run
        TechnicalState.CANCELLED,
    },
    TechnicalState.FAILED_RETRYABLE: {
        TechnicalState.REQUEUED,
        TechnicalState.FAILED_FATAL,
    },
    TechnicalState.EXPIRED: {
        TechnicalState.REQUEUED,
        TechnicalState.FAILED_FATAL,
    },
    TechnicalState.REQUEUED: {
        TechnicalState.APPROVED,
        TechnicalState.LEASED,
    },
    # Terminal states.
    TechnicalState.SUCCEEDED: set(),
    TechnicalState.FAILED_FATAL: set(),
    TechnicalState.CANCELLED: set(),
}


def can_transition(src: TechnicalState, dst: TechnicalState) -> bool:
    return dst in _TECH_TRANSITIONS.get(src, set())


def assert_transition(src: TechnicalState, dst: TechnicalState) -> None:
    """Raise IllegalTransition unless src->dst is declared legal."""
    if not can_transition(src, dst):
        raise IllegalTransition(f"{src.value} -> {dst.value} is not a legal transition")


def is_terminal(state: TechnicalState) -> bool:
    return len(_TECH_TRANSITIONS.get(state, set())) == 0


# Scientific states are NOT a function of technical states. This map records the
# only thing we are willing to assert automatically: a terminal-but-unevaluated
# task is scientifically NOT_EVALUATED. Everything else requires the science
# layer to assign a value with evidence (spec §24.5/§24.6). There is deliberately
# no helper that turns SUCCEEDED into SUPPORTED.
def default_scientific_state() -> ScientificState:
    return ScientificState.NOT_EVALUATED
