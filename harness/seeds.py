"""Deterministic seed management (spec §17).

Reproducibility requires explicit, derivable seeds. We reject the booleans
``True``/``False`` (a common accidental ``seed=True``) and reject ``None`` when a
seed is required. Child seeds are derived from the root seed by hashing a
namespace string, so the same (root, namespace) always yields the same seed.
"""
from __future__ import annotations

import hashlib
from typing import Any

# 2**32 keeps derived seeds inside the range every common RNG / CLI tool accepts.
_MASK = (1 << 32) - 1


class SeedError(ValueError):
    """Raised for invalid or missing seeds."""


def validate_seed(seed: Any, *, required: bool = True) -> int | None:
    """Validate a user-provided root seed.

    - ``True``/``False`` are rejected outright: a boolean is never a valid seed
      and almost always signals a bug (e.g. ``seed=True``).
    - ``None`` is rejected when ``required`` (reproducibility demanded), allowed
      otherwise (caller has explicitly opted into nondeterminism).
    - Otherwise it must be a non-negative integer.
    """
    if isinstance(seed, bool):
        raise SeedError(f"seed must be an int, got bool {seed!r}")
    if seed is None:
        if required:
            raise SeedError("seed=None but a seed is required for reproducibility")
        return None
    if not isinstance(seed, int):
        raise SeedError(f"seed must be an int, got {type(seed).__name__}: {seed!r}")
    if seed < 0:
        raise SeedError(f"seed must be non-negative, got {seed}")
    return seed


class SeedManager:
    """Derives per-namespace seeds deterministically from a validated root seed."""

    def __init__(self, root_seed: Any, *, required: bool = True) -> None:
        self.root_seed = validate_seed(root_seed, required=required)
        self.derivation_rule = "sha256(root_seed:namespace) -> first 8 bytes & (2**32-1)"

    def derive(self, *namespace: Any) -> int:
        """Derive a stable child seed for a namespace (e.g. dataset, task, fold)."""
        if self.root_seed is None:
            raise SeedError("cannot derive from an undefined (None) root seed")
        key = ":".join([str(self.root_seed), *(str(n) for n in namespace)])
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") & _MASK

    def record(self) -> dict[str, Any]:
        """Auditable record of the seed configuration (spec §17)."""
        return {
            "root_seed": self.root_seed,
            "derivation_rule": self.derivation_rule,
            "determinism": "complete" if self.root_seed is not None else "not_required",
        }
