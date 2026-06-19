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
from pathlib import Path

_APPLIED = False


def candidate_dirs() -> list[str]:
    dirs: list[str] = []
    for d in os.environ.get("HARNESS_TOOL_PATHS", "").split(os.pathsep):
        if d.strip():
            dirs.append(d.strip())
    # Conventional isolated env created for extra phylo tools.
    conda_root = os.environ.get("CONDA_PREFIX_1") or os.environ.get("CONDA_PREFIX")
    if conda_root:
        # CONDA_PREFIX may already be base; the envs live alongside.
        base = Path(conda_root)
        for root in {base, base.parent.parent if base.name == "envs" else base}:
            cand = root / "envs" / "phylo_extra" / "bin"
            if cand.is_dir():
                dirs.append(str(cand))
    # Also the common default install location.
    home_env = Path.home() / "miniconda3" / "envs" / "phylo_extra" / "bin"
    if home_env.is_dir():
        dirs.append(str(home_env))
    # Dedupe, keep order.
    seen: set[str] = set()
    return [d for d in dirs if not (d in seen or seen.add(d))]


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
