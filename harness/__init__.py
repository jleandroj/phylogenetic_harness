"""Phylogenetic harness — an auditable, sceptical core for AI-assisted
phylogenomics (spec §0–§25).

Guiding invariant: a result that *ran* is not a result that is *true*. Technical
state and scientific state are tracked separately and never auto-derived from one
another.
"""
from __future__ import annotations

__version__ = "0.1.0"

# Make extra tool dirs (e.g. a dedicated conda env via HARNESS_TOOL_PATHS) visible
# to detection/execution before any tool registry is built.
from .toolpaths import ensure_tool_paths as _ensure_tool_paths  # noqa: E402

_ensure_tool_paths()

from . import (  # noqa: F401,E402
    aggregate,
    approval,
    bio,
    bio_report,
    clock,
    datasets,
    diff,
    environment,
    events,
    executor,
    hardware,
    ids,
    leases,
    logging_json,
    manifest,
    phylo_guards,
    recovery,
    redaction,
    report,
    resources,
    resume,
    run,
    runner,
    sandbox,
    scheduler,
    science,
    seeds,
    states,
    task_types,
    tasks,
    taskstore,
    tools,
    validators,
    workers,
)
