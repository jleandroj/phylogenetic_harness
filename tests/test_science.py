from harness.science import (
    DEFAULT_NOT_ALLOWED,
    NegativeCategory,
    build_interpretation,
    classify_negative,
    detect_degenerate,
)
from harness.states import ScientificState
from harness.validators import CheckResult


def passed(name="c"):
    return CheckResult(name, "PASSED")


def failed(name="c"):
    return CheckResult(name, "FAILED")


# ---- negative classification (spec §24.7) ----

def test_negative_technical_failure():
    n = classify_negative(technical_success=False, input_quality_ok=True, has_signal=True,
                          support_above_threshold=True, model_appropriate=True, conflicting=False)
    assert n.category == NegativeCategory.TECHNICAL_FAILURE


def test_negative_bad_input():
    n = classify_negative(technical_success=True, input_quality_ok=False, has_signal=True,
                          support_above_threshold=True, model_appropriate=True, conflicting=False)
    assert n.category == NegativeCategory.BAD_INPUT


def test_negative_insufficient_signal():
    n = classify_negative(technical_success=True, input_quality_ok=True, has_signal=False,
                          support_above_threshold=True, model_appropriate=True, conflicting=False)
    assert n.category == NegativeCategory.INSUFFICIENT_SIGNAL


def test_negative_true_negative_possible():
    n = classify_negative(technical_success=True, input_quality_ok=True, has_signal=True,
                          support_above_threshold=True, model_appropriate=True, conflicting=False)
    assert n.category == NegativeCategory.TRUE_NEGATIVE_POSSIBLE
    # A negative is never auto-marked as a failure.
    assert n.technical_success is True


# ---- degenerate detection (spec §24.8) ----

def test_degenerate_empty_output():
    r = detect_degenerate(output_size_bytes=0)
    assert r.degenerate and "empty" in r.reasons[0]


def test_degenerate_constant_metric():
    r = detect_degenerate(metrics=[1.0, 1.0, 1.0])
    assert r.degenerate
    assert any("constant" in x or "1.0" in x for x in r.reasons)


def test_degenerate_identical_hashes():
    r = detect_degenerate(all_hashes=["h", "h", "h"], hashes_expected_distinct=True)
    assert r.degenerate


def test_not_degenerate_normal():
    r = detect_degenerate(output_size_bytes=1024, metrics=[0.3, 0.7], n_records=5)
    assert not r.degenerate


# ---- three-level interpretation (spec §24.5) ----

def test_technical_pass_is_not_biological_pass():
    """The core invariant: passing technical validators does NOT make a result
    biologically interpretable."""
    interp = build_interpretation([passed(), passed()])
    assert interp.technical.status == "PASSED"
    assert interp.biological.status == "LIMITED"
    assert interp.scientific_state == ScientificState.LOW_CONFIDENCE
    assert interp.confidence == "low"


def test_degenerate_overrides_to_degenerate_state():
    interp = build_interpretation(
        [passed()], degeneracy=detect_degenerate(output_size_bytes=0)
    )
    assert interp.scientific_state == ScientificState.DEGENERATE
    assert interp.biological.status == "FAILED"


def test_technical_failure_not_interpretable():
    interp = build_interpretation([failed()])
    assert interp.scientific_state == ScientificState.NOT_BIOLOGICALLY_INTERPRETABLE


def test_statistical_support_enables_interpretability():
    interp = build_interpretation([passed()], statistical_checks=[passed("bootstrap")])
    assert interp.scientific_state == ScientificState.BIOLOGICALLY_INTERPRETABLE
    assert interp.confidence == "medium"


def test_standing_prohibitions_always_present():
    interp = build_interpretation([passed()])
    for clause in DEFAULT_NOT_ALLOWED:
        assert clause in interp.interpretation_not_allowed


def test_negative_result_maps_to_scientific_state():
    neg = classify_negative(technical_success=True, input_quality_ok=True, has_signal=True,
                           support_above_threshold=True, model_appropriate=True, conflicting=False)
    interp = build_interpretation([passed()], negative=neg)
    assert interp.scientific_state == ScientificState.NEGATIVE_RESULT
