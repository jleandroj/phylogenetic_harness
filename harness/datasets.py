"""Dataset manifests (spec §4, §12, §24.1).

No analysis runs without a manifest. Each input gets a recorded checksum, size
and quality status; excluded taxa carry an explicit reason. Quality status is
free to be ``limited``/``unknown``/``failed`` but low-quality inputs must be
*marked as such*, never silently treated as clean (spec §4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import ids

QUALITY_STATUSES = {"validated", "limited", "unknown", "failed"}
ASSEMBLY_LEVELS = {"chromosome", "scaffold", "contig", "complete", "partial", "unknown"}


class ManifestError(ValueError):
    """Raised when a manifest is missing required structure."""


class MissingManifestError(Exception):
    """Raised when an analysis is attempted without any manifest."""


@dataclass
class DatasetInput:
    sample_id: str
    path: str
    format: str
    source: str = "local"
    assembly_level: str = "unknown"
    quality_status: str = "unknown"
    checksum: str | None = None
    size_bytes: int | None = None
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DatasetInput:
        missing = [k for k in ("sample_id", "path", "format") if not d.get(k)]
        if missing:
            raise ManifestError(f"input missing required field(s): {missing}")
        # Path confinement (audit P2.9): a manifest must not point outside its own
        # directory. Reject absolute paths and parent traversal so checksumming
        # cannot read arbitrary files.
        path = str(d["path"])
        if Path(path).is_absolute() or ".." in Path(path).parts:
            raise ManifestError(
                f"input path {path!r} must be relative to the manifest and may not use '..'"
            )
        qs = d.get("quality_status", "unknown")
        if qs not in QUALITY_STATUSES:
            raise ManifestError(f"invalid quality_status {qs!r}; allowed: {QUALITY_STATUSES}")
        return cls(
            sample_id=d["sample_id"],
            path=d["path"],
            format=d["format"],
            source=d.get("source", "local"),
            assembly_level=d.get("assembly_level", "unknown"),
            quality_status=qs,
            checksum=d.get("checksum"),
            size_bytes=d.get("size_bytes"),
            notes=d.get("notes", ""),
        )

    def compute_checksum(self, base_dir: Path) -> DatasetInput:
        """Fill checksum + size from the file on disk, resolved against base_dir.

        Defence in depth (audit P2.9): even though from_dict rejects absolute/`..`
        paths, verify the resolved path stays within base_dir before reading."""
        base = Path(base_dir).resolve()
        p = (base / self.path).resolve()
        if base not in p.parents and p != base:
            raise ManifestError(f"resolved input path {p} escapes base dir {base}")
        if p.exists() and p.is_file():
            self.checksum = "sha256:" + ids.sha256_file(p)
            self.size_bytes = p.stat().st_size
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "path": self.path,
            "format": self.format,
            "source": self.source,
            "assembly_level": self.assembly_level,
            "quality_status": self.quality_status,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "notes": self.notes,
        }


@dataclass
class DatasetManifest:
    dataset_id: str
    dataset_type: str
    scientific_question: str
    inputs: list[DatasetInput] = field(default_factory=list)
    taxa_include: list[str] = field(default_factory=list)
    taxa_exclude: list[dict[str, str]] = field(default_factory=list)
    outgroups_selected: list[str] = field(default_factory=list)
    outgroups_alternatives: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    created_at: str | None = None
    created_by: str | None = None
    base_dir: Path = field(default_factory=lambda: Path("."))

    @classmethod
    def from_dict(cls, d: dict[str, Any], base_dir: str | Path = ".") -> DatasetManifest:
        for key in ("dataset_id", "dataset_type", "scientific_question"):
            if not d.get(key):
                raise ManifestError(f"manifest missing required field: {key}")
        taxa = d.get("taxa", {}) or {}
        outgroups = d.get("outgroups", {}) or {}
        return cls(
            dataset_id=d["dataset_id"],
            dataset_type=d["dataset_type"],
            scientific_question=d["scientific_question"],
            inputs=[DatasetInput.from_dict(i) for i in (d.get("inputs") or [])],
            taxa_include=list(taxa.get("include", [])),
            taxa_exclude=list(taxa.get("exclude", [])),
            outgroups_selected=list(outgroups.get("selected", [])),
            outgroups_alternatives=list(outgroups.get("alternatives", [])),
            limitations=list(d.get("limitations", [])),
            created_at=d.get("created_at"),
            created_by=d.get("created_by"),
            base_dir=Path(base_dir),
        )

    @classmethod
    def load(cls, path: str | Path) -> DatasetManifest:
        p = Path(path)
        if not p.exists():
            raise MissingManifestError(f"no dataset manifest at {p}")
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not data:
            raise ManifestError(f"empty manifest at {p}")
        return cls.from_dict(data, base_dir=p.parent)

    def compute_checksums(self) -> DatasetManifest:
        for inp in self.inputs:
            inp.compute_checksum(self.base_dir)
        return self

    def low_quality_inputs(self) -> list[DatasetInput]:
        return [i for i in self.inputs if i.quality_status in ("limited", "failed", "unknown")]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_type": self.dataset_type,
            "scientific_question": self.scientific_question,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "inputs": [i.to_dict() for i in self.inputs],
            "taxa": {"include": self.taxa_include, "exclude": self.taxa_exclude},
            "outgroups": {
                "selected": self.outgroups_selected,
                "alternatives": self.outgroups_alternatives,
            },
            "limitations": self.limitations,
        }
