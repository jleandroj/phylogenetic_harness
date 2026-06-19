"""Phylogenetic harness — an auditable, sceptical core for AI-assisted
phylogenomics (spec §0–§25).

Guiding invariant: a result that *ran* is not a result that is *true*. Technical
state and scientific state are tracked separately and never auto-derived from one
another.
"""
from __future__ import annotations

__version__ = "0.1.0"

from . import (  # noqa: F401
    approval,
    clock,
    datasets,
    environment,
    events,
    executor,
    hardware,
    ids,
    leases,
    logging_json,
    recovery,
    redaction,
    report,
    resources,
    run,
    runner,
    science,
    seeds,
    states,
    tasks,
    tools,
    validators,
    workers,
)
