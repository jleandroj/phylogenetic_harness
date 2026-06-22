"""Iter 8 (round 2): filesystem write-confinement (containment guarantee #3).

A bad-faith task could declare an output path that overwrites data it must never
touch — the read-only source genomes (irreplaceable), the user's dotfiles, or
system files — or use ``..``/symlinks to escape its workspace.

We enforce three rules on every declared output, BEFORE the tool runs:
  * no ``..`` traversal in the path;
  * never under a built-in SYSTEM root (/etc, /usr, /bin, /boot, ...);
  * never under an operator-configured PROTECTED root (e.g. the genomes dir).

This is a denylist of things that must stay untouched rather than a whitelist of
the one scratch dir, so legitimate pipelines/replays that write to operator-chosen
locations keep working, while the irreplaceable data is hard-protected.
"""
from __future__ import annotations

import os
from pathlib import Path

# Clearly-system locations a scientific tool should never write into.
SYSTEM_ROOTS: tuple[str, ...] = (
    "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot",
    "/sys", "/proc", "/dev", "/root", "/var",
)


def _resolved(p: Path) -> Path:
    """Resolve the deepest existing ancestor (so a not-yet-created output is still
    checked against its real parent, defeating symlink escapes), then re-attach
    the unresolved tail."""
    cur = p if p.exists() else p.parent
    while not cur.exists() and cur != cur.parent:
        cur = cur.parent
    try:
        real = cur.resolve()
    except OSError:
        real = cur
    try:
        tail = p.relative_to(cur)
    except ValueError:
        tail = Path(p.name)
    return real / tail


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def check_output_confinement(
    outputs: list[str], run_dir: str | os.PathLike[str] | None = None, *,
    protected_roots: tuple[str, ...] = (), deny_system: bool = True,
) -> list[str]:
    """Return a list of violations (empty = every output is safe to write)."""
    roots: list[tuple[str, Path]] = []
    for r in protected_roots:
        try:
            roots.append(("protected", Path(r).resolve()))
        except OSError:
            continue
    if deny_system:
        for r in SYSTEM_ROOTS:
            roots.append(("system", Path(r)))

    violations: list[str] = []
    for out in outputs:
        op = Path(out)
        if ".." in op.parts:
            violations.append(f"output path uses traversal '..': {out}")
            continue
        rp = _resolved(op)
        for kind, root in roots:
            if _under(rp, root):
                violations.append(f"output writes into {kind} root {root}: {out}")
                break
    return violations
