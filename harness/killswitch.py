"""Kill-switch + action allowlist (production guarantee #3: containment).

The harness must be able to STOP an agent that misbehaves, and must refuse any
action not explicitly permitted — never assume the agent's good faith.

Kill-switch: a STOP marker file. The runner checks before every task and aborts
if present. Two scopes:
  * per-run:   runs/{run}/STOP
  * global:    ~/.harness/STOP (or $HARNESS_HOME/STOP) — panic, stops everything.

Allowlist: a run may declare ``tool_allowlist``; the runner then refuses any tool
not on it (on top of the registry gate).
"""
from __future__ import annotations

import os
from pathlib import Path


def _global_stop() -> Path:
    home = os.environ.get("HARNESS_HOME")
    base = Path(home) if home else Path.home() / ".harness"
    return base / "STOP"


def is_stopped(run_dir: str | Path | None = None) -> tuple[bool, str | None]:
    """Return (stopped, scope). True if a global or per-run STOP marker exists."""
    if _global_stop().exists():
        return True, "global"
    if run_dir is not None and (Path(run_dir) / "STOP").exists():
        return True, "run"
    return False, None


def stop(run_dir: str | Path, *, reason: str = "operator kill-switch") -> Path:
    p = Path(run_dir) / "STOP"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(reason + "\n", encoding="utf-8")
    return p


def panic(*, reason: str = "operator panic — stop ALL runs") -> Path:
    p = _global_stop()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(reason + "\n", encoding="utf-8")
    return p


def clear(run_dir: str | Path | None = None, *, glob: bool = False) -> None:
    if glob and _global_stop().exists():
        _global_stop().unlink()
    if run_dir is not None and (Path(run_dir) / "STOP").exists():
        (Path(run_dir) / "STOP").unlink()


class ActionBlocked(Exception):
    """Raised when a task is blocked by the kill-switch or the action allowlist."""
