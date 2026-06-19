"""Validator registry and built-in validators (spec §18, §24.5).

Validators check *technical* validity only — that a file exists, parses, has the
expected taxa. Passing every technical validator says nothing about biological
correctness; that separation is enforced in ``harness.science``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VALID_DNA = set("ACGTNUacgtnu-.RYSWKMBDHVryswkmbdhvNn")


@dataclass
class CheckResult:
    name: str
    status: str  # PASSED | FAILED | NOT_APPLICABLE
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "PASSED"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail, "data": self.data}


# ---- file-level checks -------------------------------------------------------

def file_exists(path: str | Path, **_: Any) -> CheckResult:
    p = Path(path)
    ok = p.exists() and p.is_file()
    return CheckResult("file_exists", "PASSED" if ok else "FAILED", str(p))


def file_nonempty(path: str | Path, **_: Any) -> CheckResult:
    p = Path(path)
    if not p.exists():
        return CheckResult("file_nonempty", "FAILED", f"missing: {p}")
    size = p.stat().st_size
    return CheckResult(
        "file_nonempty", "PASSED" if size > 0 else "FAILED", f"{size} bytes", {"size": size}
    )


# ---- FASTA -------------------------------------------------------------------

def _parse_fasta(path: Path) -> tuple[list[str], dict[str, int], set[str]]:
    names: list[str] = []
    lengths: dict[str, int] = {}
    bad_chars: set[str] = set()
    current = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith(">"):
                parts = line[1:].split()
                current = parts[0] if parts else ""  # tolerate a bare ">" header
                names.append(current)
                lengths[current] = 0
            elif current is not None:
                lengths[current] += len(line)
                for ch in line:
                    if ch not in VALID_DNA:
                        bad_chars.add(ch)
    return names, lengths, bad_chars


def fasta_valid(path: str | Path, **_: Any) -> CheckResult:
    p = Path(path)
    if not p.exists():
        return CheckResult("fasta_valid", "FAILED", f"missing: {p}")
    try:
        names, lengths, bad = _parse_fasta(p)
    except (OSError, UnicodeDecodeError) as exc:
        return CheckResult("fasta_valid", "FAILED", f"unreadable: {exc}")
    if not names:
        return CheckResult("fasta_valid", "FAILED", "no sequences found")
    if len(names) != len(set(names)):
        dups = sorted({n for n in names if names.count(n) > 1})
        return CheckResult("fasta_valid", "FAILED", f"duplicate names: {dups}")
    if bad:
        return CheckResult("fasta_valid", "FAILED", f"invalid residues: {sorted(bad)}")
    if any(v == 0 for v in lengths.values()):
        empties = [n for n, v in lengths.items() if v == 0]
        return CheckResult("fasta_valid", "FAILED", f"empty sequences: {empties}")
    return CheckResult(
        "fasta_valid",
        "PASSED",
        f"{len(names)} sequences",
        {"names": names, "lengths": lengths},
    )


def alignment_valid(path: str | Path, *, min_sequences: int = 2, **_: Any) -> CheckResult:
    """A multiple sequence alignment: valid FASTA, >=min_sequences, all equal length."""
    base = fasta_valid(path)
    if not base.passed:
        return CheckResult("alignment_valid", "FAILED", f"not valid FASTA: {base.detail}")
    lengths = base.data["lengths"]
    n = len(lengths)
    if n < min_sequences:
        return CheckResult("alignment_valid", "FAILED", f"only {n} sequences (< {min_sequences})")
    distinct = set(lengths.values())
    if len(distinct) != 1:
        return CheckResult("alignment_valid", "FAILED", f"unequal lengths: {sorted(distinct)}")
    width = next(iter(distinct))
    return CheckResult("alignment_valid", "PASSED", f"{n} sequences x {width} columns",
                       {"n_sequences": n, "width": width})


# ---- Newick tree -------------------------------------------------------------

def _newick_taxa(text: str) -> list[str]:
    """Extract leaf labels from a Newick string (no external deps)."""
    taxa: list[str] = []
    token = ""
    for ch in text:
        if ch in "(),;:":
            if token.strip():
                # strip support/branch length: take label part before ':'
                label = token.strip().split(":")[0]
                if label and not label.replace(".", "").isdigit():
                    taxa.append(label)
            token = ""
            if ch == ":":
                # consume until next structural char handled by outer loop;
                # we simply blank the token so the branch length isn't a label.
                token = ""
        else:
            token += ch
    return taxa


def _newick_balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _newick_taxa_dendropy(text: str) -> list[str] | None:
    """Parse leaf labels with dendropy if available (correct: handles quoted
    labels, comments, numeric labels). Returns None if dendropy is absent."""
    try:
        import dendropy
    except ImportError:
        return None
    # preserve_underscores: keep "homo_sapiens" intact instead of the Newick-standard
    # underscore->space conversion, since taxon IDs commonly use underscores.
    tree = dendropy.Tree.get(
        data=text, schema="newick", suppress_internal_node_taxa=True, preserve_underscores=True
    )
    return [leaf.taxon.label for leaf in tree.leaf_node_iter() if leaf.taxon is not None]


def newick_valid(path: str | Path, *, expected_taxa: list[str] | None = None, **_: Any) -> CheckResult:
    p = Path(path)
    if not p.exists():
        return CheckResult("newick_valid", "FAILED", f"missing: {p}")
    text = p.read_text(encoding="utf-8").strip()
    if not text.endswith(";"):
        return CheckResult("newick_valid", "FAILED", "Newick must end with ';'")
    if not _newick_balanced(text):
        return CheckResult("newick_valid", "FAILED", "unbalanced parentheses")

    # Prefer a real phylogenetics parser; fall back to the approximate tokenizer.
    engine = "dendropy"
    try:
        taxa = _newick_taxa_dendropy(text)
    except Exception as exc:  # dendropy raises its own error hierarchy on malformed input
        return CheckResult("newick_valid", "FAILED", f"dendropy parse error: {exc}", {"engine": engine})
    if taxa is None:
        engine = "fallback-approx"
        taxa = _newick_taxa(text)
    if not taxa:
        return CheckResult("newick_valid", "FAILED", "no taxa parsed", {"engine": engine})
    data = {"taxa": taxa, "engine": engine}
    if expected_taxa is not None:
        present = set(taxa)
        missing = [t for t in expected_taxa if t not in present]
        extra = [t for t in taxa if t not in set(expected_taxa)]
        data.update({"missing": missing, "extra": extra})
        if missing:
            return CheckResult("newick_valid", "FAILED", f"missing expected taxa: {missing}", data)
    return CheckResult("newick_valid", "PASSED", f"{len(taxa)} taxa", data)


# ---- VCF (header-level, no bcftools dependency) ------------------------------

def vcf_header_valid(path: str | Path, **_: Any) -> CheckResult:
    p = Path(path)
    if not p.exists():
        return CheckResult("vcf_header_valid", "FAILED", f"missing: {p}")
    saw_fileformat = saw_chrom = False
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("##fileformat=VCF"):
                saw_fileformat = True
            elif line.startswith("#CHROM"):
                saw_chrom = True
                break
            elif not line.startswith("#"):
                break
    if saw_fileformat and saw_chrom:
        return CheckResult("vcf_header_valid", "PASSED", "fileformat + #CHROM present")
    missing = []
    if not saw_fileformat:
        missing.append("##fileformat")
    if not saw_chrom:
        missing.append("#CHROM")
    return CheckResult("vcf_header_valid", "FAILED", f"missing header lines: {missing}")


class ValidatorRegistry:
    """Name -> validator callable. Each callable takes (path, **kwargs)->CheckResult."""

    def __init__(self) -> None:
        self._validators: dict[str, Callable[..., CheckResult]] = {}
        for name, fn in {
            "file_exists": file_exists,
            "file_nonempty": file_nonempty,
            "fasta_valid": fasta_valid,
            "alignment_valid": alignment_valid,
            "newick_valid": newick_valid,
            "vcf_header_valid": vcf_header_valid,
        }.items():
            self._validators[name] = fn

    def register(self, name: str, fn: Callable[..., CheckResult]) -> None:
        self._validators[name] = fn

    def get(self, name: str) -> Callable[..., CheckResult]:
        if name not in self._validators:
            raise KeyError(f"unknown validator {name!r}")
        return self._validators[name]

    def run(self, name: str, path: str | Path, **kwargs: Any) -> CheckResult:
        return self.get(name)(path, **kwargs)

    def run_many(
        self, names: list[str], path: str | Path, **kwargs: Any
    ) -> list[CheckResult]:
        return [self.run(n, path, **kwargs) for n in names]
