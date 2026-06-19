import pytest

from harness.seeds import SeedError, SeedManager, validate_seed


@pytest.mark.parametrize("bad", [True, False])
def test_reject_boolean_seed(bad):
    with pytest.raises(SeedError):
        validate_seed(bad)


def test_reject_none_when_required():
    with pytest.raises(SeedError):
        validate_seed(None, required=True)


def test_allow_none_when_not_required():
    assert validate_seed(None, required=False) is None


def test_reject_negative():
    with pytest.raises(SeedError):
        validate_seed(-5)


def test_derivation_is_deterministic():
    a = SeedManager(42)
    b = SeedManager(42)
    assert a.derive("dataset", "task_1") == b.derive("dataset", "task_1")


def test_derivation_varies_by_namespace():
    m = SeedManager(42)
    assert m.derive("task_1") != m.derive("task_2")


def test_derive_in_range():
    m = SeedManager(7)
    s = m.derive("x")
    assert 0 <= s < 2 ** 32


def test_record_reports_complete_determinism():
    assert SeedManager(1).record()["determinism"] == "complete"
    assert SeedManager(None, required=False).record()["determinism"] == "not_required"
