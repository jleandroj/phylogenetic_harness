"""Phylogenetic harness — an auditable, sceptical core for AI-assisted
phylogenomics (spec §0–§25).

Guiding invariant: a result that *ran* is not a result that is *true*. Technical
state and scientific state are tracked separately and never auto-derived from one
another.
"""
from __future__ import annotations

__version__ = "0.1.0"

# NOTE: importing the package no longer mutates PATH (audit round 4). Call
# harness.toolpaths.ensure_tool_paths() explicitly (the CLI does) to make
# HARNESS_TOOL_PATHS dirs visible to tool detection/execution.

from . import (  # noqa: F401,E402
    aggregate,
    approval,
    audit,
    bio,
    bio_report,
    clock,
    datasets,
    diff,
    environment,
    events,
    executor,
    genome_phylo,
    hardware,
    hooks,
    ids,
    leases,
    logging_json,
    manifest,
    phylo_guards,
    policy,
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
    toolpaths,
    tools,
    trimtool,
    validators,
    workers,
)
