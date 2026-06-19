"""Audit P1.7 (per-attempt logs), P1.8 (robust Newick), P1.9 (seeds/lockfile)."""
import sys

from harness.clock import counting_clock
from harness.executor import LocalExecutor
from harness.seeds import SeedManager
from harness.validators import newick_valid

PY = sys.executable


# ---- P1.7: retries do not clobber prior attempt logs ----

def test_retry_keeps_previous_attempt_log(run_dir):
    ex = LocalExecutor(run_dir, clock_fn=counting_clock(), disk_path=run_dir)
    r1 = ex.run("task", [PY, "-c", "print('attempt one')"], attempt=1)
    r2 = ex.run("task", [PY, "-c", "print('attempt two')"], attempt=2)
    assert r1.stdout_path != r2.stdout_path
    assert "attempt one" in open(r1.stdout_path).read()   # still there after retry
    assert "attempt two" in open(r2.stdout_path).read()


# ---- P1.8: Newick parser handles quoted labels, comments, numeric labels ----

def test_newick_quoted_labels(tmp_path):
    p = tmp_path / "q.nwk"
    p.write_text("(('Homo sapiens':0.1,'Pan troglodytes':0.1):0.2,Pongo:0.3);")
    r = newick_valid(p)
    assert r.passed
    assert "Homo sapiens" in r.data["taxa"]


def test_newick_numeric_labels_not_dropped(tmp_path):
    p = tmp_path / "n.nwk"
    p.write_text("((123:0.1,456:0.1):0.2,789:0.3);")
    r = newick_valid(p)
    assert r.passed
    # With dendropy these numeric tip labels are real taxa, not branch lengths.
    if r.data["engine"] == "dendropy":
        assert set(r.data["taxa"]) == {"123", "456", "789"}


def test_newick_with_comments(tmp_path):
    p = tmp_path / "c.nwk"
    p.write_text("((A[&rate=1.0]:0.1,B:0.1):0.2,C:0.3);")
    r = newick_valid(p)
    assert r.passed
    assert {"A", "B", "C"} <= set(r.data["taxa"])


def test_newick_engine_recorded(tmp_path):
    p = tmp_path / "e.nwk"
    p.write_text("(A:0.1,B:0.2);")
    r = newick_valid(p)
    assert r.data["engine"] in ("dendropy", "fallback-approx")


# ---- P1.9: deterministic seed derivation ----

def test_seed_derivation_deterministic():
    assert SeedManager(42).derive("run", "task_1") == SeedManager(42).derive("run", "task_1")
    assert SeedManager(42).derive("run", "task_1") != SeedManager(42).derive("run", "task_2")
