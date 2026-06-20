"""Make extra tool directories discoverable (audit-friendly tool provisioning).

Binaries installed into a dedicated conda env (or an HPC module dir) are not on
the default PATH. Rather than hardcode anything, this prepends directories from
the ``HARNESS_TOOL_PATHS`` env var (colon-separated) to ``PATH`` once, so both
detection (``shutil.which``) and execution (the executor inherits ``os.environ``)
find them with bare names. Optionally also picks up a conventional
``phylo_extra`` conda env if present.

Call ``ensure_tool_paths()`` early; it is idempotent.
"""
from __future__ import annotations

import os

_APPLIED = False


def candidate_dirs() -> list[str]:
    """Only EXPLICIT directories from ``HARNESS_TOOL_PATHS`` (audit round 4: no
    silent HOME/conda auto-prepend — that shadowed system tools and was a
    supply-chain risk)."""
    dirs: list[str] = []
    for d in os.environ.get("HARNESS_TOOL_PATHS", "").split(os.pathsep):
        if d.strip():
            dirs.append(d.strip())
    # Dedupe, keep order.
    seen: set[str] = set()
    out: list[str] = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def ensure_tool_paths() -> list[str]:
    """Prepend candidate tool dirs to PATH (idempotent). Returns the dirs added."""
    global _APPLIED
    added = candidate_dirs()
    if added:
        current = os.environ.get("PATH", "")
        parts = current.split(os.pathsep)
        new = [d for d in added if d not in parts]
        if new:
            os.environ["PATH"] = os.pathsep.join(new + parts)
    _APPLIED = True
    return added
