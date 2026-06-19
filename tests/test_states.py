import pytest

from harness.states import (
    IllegalTransition,
    ScientificState,
    TechnicalState,
    assert_transition,
    can_transition,
    default_scientific_state,
    is_terminal,
)


def test_legal_transition_passes():
    assert_transition(TechnicalState.PENDING, TechnicalState.APPROVED)
    assert_transition(TechnicalState.RUNNING, TechnicalState.SUCCEEDED)


def test_illegal_transition_raises():
    with pytest.raises(IllegalTransition):
        assert_transition(TechnicalState.PENDING, TechnicalState.SUCCEEDED)
    with pytest.raises(IllegalTransition):
        assert_transition(TechnicalState.SUCCEEDED, TechnicalState.RUNNING)


def test_terminal_states_have_no_exits():
    assert is_terminal(TechnicalState.SUCCEEDED)
    assert is_terminal(TechnicalState.FAILED_FATAL)
    assert not is_terminal(TechnicalState.RUNNING)


def test_running_can_expire_for_recovery():
    assert can_transition(TechnicalState.RUNNING, TechnicalState.EXPIRED)
    assert can_transition(TechnicalState.EXPIRED, TechnicalState.REQUEUED)


def test_technical_and_scientific_are_separate_namespaces():
    # The whole point: there is no SUCCEEDED member in ScientificState, and no
    # SUPPORTED member in TechnicalState. "Ran" and "true" cannot be confused.
    assert not hasattr(ScientificState, "SUCCEEDED")
    assert not hasattr(TechnicalState, "SUPPORTED")


def test_default_scientific_state_is_not_evaluated():
    assert default_scientific_state() == ScientificState.NOT_EVALUATED
