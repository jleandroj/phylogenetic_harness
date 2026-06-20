"""Built-in alignment trimmer (audit round 4 #6).

Removes alignment columns whose gap fraction exceeds a threshold (gappy-column /
occupancy filter) — the most common, defensible MSA cleanup, so downstream trees
are not built on poorly-aligned columns. A real external tool (trimAl) is
registered as a contract; when absent this built-in is used and recorded as such.

Runs as a task through the executor so it stays in the audit trail:
    python -m harness.trimtool <in.fasta> <out.fasta> [max_gap_fraction]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

GAP = set("-.")


def _read_fasta(path: Path) -> list[tuple[str, str]]:
    recs: list[tuple[str, str]] = []
    name: str | None = None
    seq: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(">"):
            if name is not None:
                recs.append((name, "".join(seq)))
            name, seq = line[1:].split()[0] if line[1:].split() else "", []
        elif name is not None:
            seq.append(line.strip())
    if name is not None:
        recs.append((name, "".join(seq)))
    return recs


def trim_alignment(in_fasta: str | Path, out_fasta: str | Path,
                   max_gap_fraction: float = 0.5) -> dict[str, Any]:
    """Keep only columns with gap fraction <= max_gap_fraction. Returns stats."""
    recs = _read_fasta(Path(in_fasta))
    if not recs:
        Path(out_fasta).write_text("", encoding="utf-8")
        return {"n_sequences": 0, "columns_before": 0, "columns_after": 0, "removed": 0}
    width = len(recs[0][1])
    n = len(recs)
    keep = []
    for col in range(width):
        gaps = sum(1 for _, s in recs if col < len(s) and s[col] in GAP)
        if (gaps / n) <= max_gap_fraction:
            keep.append(col)
    out = []
    for name, s in recs:
        trimmed = "".join(s[c] for c in keep if c < len(s))
        out.append(f">{name}\n{trimmed}\n")
    Path(out_fasta).write_text("".join(out), encoding="utf-8")
    return {
        "n_sequences": n,
        "columns_before": width,
        "columns_after": len(keep),
        "removed": width - len(keep),
        "max_gap_fraction": max_gap_fraction,
    }


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        sys.stderr.write("usage: python -m harness.trimtool <in.fasta> <out.fasta> [max_gap_fraction]\n")
        return 2
    in_fa, out_fa = argv[0], argv[1]
    thr = float(argv[2]) if len(argv) > 2 else 0.5
    stats = trim_alignment(in_fa, out_fa, max_gap_fraction=thr)
    sys.stdout.write(json.dumps(stats) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
