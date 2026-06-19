"""Identifier and hashing utilities (spec §8, §25).

Provenance requires stable, deterministic IDs and content hashes. Time-derived
IDs (run IDs) accept an explicit timestamp so callers control reproducibility;
content/config/command hashes are pure functions of their input.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

_HEX = "0123456789abcdef"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str | os.PathLike[str], chunk: int = 1 << 20) -> str:
    """Streaming sha256 of a file's contents (does not load it all in memory)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _canonical(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def config_hash(config: dict[str, Any]) -> str:
    """Stable hash of a config dict, independent of key ordering (spec §8)."""
    return sha256_text(_canonical(config))


def command_hash(command: str | list[str]) -> str:
    """Stable hash of an exact command (string or argv list)."""
    if isinstance(command, list):
        command = "\x00".join(command)
    return sha256_text(command)


def short(h: str, n: int = 12) -> str:
    return h[:n]


def run_id(timestamp_iso: str, suffix: str = "001") -> str:
    """Build a run id from an explicit ISO timestamp (spec forbids implicit clocks
    inside reproducible code paths). E.g. ``run_20260619T131500_001``."""
    # Strip a trailing timezone offset (±HH:MM / ±HHMM / Z) and fractional seconds
    # before compacting, so the negative-offset dash isn't confused with date dashes.
    stripped = re.sub(r"([+-]\d{2}:?\d{2}|Z)$", "", timestamp_iso).split(".")[0]
    compact = stripped.replace("-", "").replace(":", "")
    return f"run_{compact}_{suffix}"


def task_id(run: str, index: int) -> str:
    return f"{run}.task_{index:06d}"


def worker_id(run: str, index: int) -> str:
    return f"{run}.worker_{index:03d}"
