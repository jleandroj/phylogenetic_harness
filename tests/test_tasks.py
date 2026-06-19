import pytest

from harness.states import IllegalTransition, ScientificState, TechnicalState
from harness.tasks import Task


def make(**kw):
    defaults = dict(
        task_id="t1", run_id="r1", task_type="x", tool_id="echo",
        command_template="echo {x}", inputs=["i"], outputs_expected=["o"],
        validators=["file_exists"], params={"x": "1"},
    )
    defaults.update(kw)
    return Task(**defaults)


def test_incomplete_task_rejected():
    with pytest.raises(ValueError):
        make(validators=[])
    with pytest.raises(ValueError):
        make(inputs=[])
    with pytest.raises(ValueError):
        make(outputs_expected=[])


def test_render_command():
    assert make().render_command() == "echo 1"


def test_state_transition_guarded():
    t = make()
    t.set_technical(TechnicalState.APPROVED)
    with pytest.raises(IllegalTransition):
        t.set_technical(TechnicalState.SUCCEEDED)  # APPROVED -> SUCCEEDED illegal


def test_scientific_state_independent():
    t = make()
    t.set_technical(TechnicalState.APPROVED)
    t.set_technical(TechnicalState.LEASED)
    t.set_technical(TechnicalState.RUNNING)
    t.set_technical(TechnicalState.SUCCEEDED)
    # Technically done, but scientific state was never auto-promoted.
    assert t.status_scientific == ScientificState.NOT_EVALUATED
