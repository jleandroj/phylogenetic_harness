"""Audit P3.10: validators must never crash on arbitrary input — they return a
CheckResult, never raise. Uses hypothesis if available, else a static corpus."""
import pytest

from harness.validators import CheckResult, fasta_valid, newick_valid, vcf_header_valid

try:
    from hypothesis import given, settings
    from hypothesis import strategies as st
    HAVE_HYPOTHESIS = True
except ImportError:
    HAVE_HYPOTHESIS = False

VALIDATORS = [fasta_valid, newick_valid, vcf_header_valid]

STATIC_CORPUS = [
    "", ">", ">a", ">a\n", "(((", "));", "A:B:C;", "\x00\x01\x02",
    "##fileformat", ">seq\n\n\n", "(a,b)" * 1000, "🧬🧬;", ">x\nACGT" * 100,
]


def _check_no_crash(text, tmp_path, name="f"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    for v in VALIDATORS:
        result = v(p)
        assert isinstance(result, CheckResult)
        assert result.status in ("PASSED", "FAILED", "NOT_APPLICABLE")


@pytest.mark.parametrize("text", STATIC_CORPUS)
def test_static_corpus_never_crashes(text, tmp_path):
    _check_no_crash(text, tmp_path)


if HAVE_HYPOTHESIS:

    @settings(max_examples=200, deadline=None)
    @given(st.text())
    def test_fuzz_validators_never_crash(text):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f"
            p.write_text(text, encoding="utf-8")
            for v in VALIDATORS:
                assert isinstance(v(p), CheckResult)
