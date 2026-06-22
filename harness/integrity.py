"""Iter 10 (round 2): input integrity verification (traceability + robustness).

Two jobs, both fail-closed:

  * **Provenance** — checksum every declared input that exists, so a run records
    exactly which bytes it consumed (a result can be tied to its real inputs, and
    a later change is detectable).
  * **Tamper / corruption guard** — if a task carries a baseline of expected input
    hashes, verify the on-disk bytes match BEFORE running. A mismatch means the
    source data (e.g. the read-only, irreplaceable genomes) changed under us —
    silent corruption, an accidental overwrite, or tampering — and the harness must
    refuse to run on it and alert the operator, not produce science from mutated data.
"""
from __future__ import annotations

from pathlib import Path

from . import ids


def hash_inputs(inputs: list[str]) -> dict[str, str | None]:
    """sha256 of each input that is an existing file (None if missing/not a file)."""
    out: dict[str, str | None] = {}
    for inp in inputs:
        p = Path(inp)
        if p.exists() and p.is_file():
            out[inp] = "sha256:" + ids.sha256_file(p)
        else:
            out[inp] = None
    return out


def verify_inputs(inputs: list[str], expected: dict[str, str]) -> list[str]:
    """Return violations: inputs whose current bytes differ from the baseline."""
    actual = hash_inputs(inputs)
    violations: list[str] = []
    for path, want in expected.items():
        have = actual.get(path)
        if have is None:
            violations.append(f"expected input is missing or unreadable: {path}")
        elif have != want:
            violations.append(f"input changed since baseline: {path} ({want} -> {have})")
    return violations
