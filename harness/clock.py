"""Clock helpers.

Real timestamps come from here so the rest of the code can accept an injected
``clock`` callable and stay deterministic under test. ``iso_now`` is the only
place that reads the wall clock for event timestamps; ``monotonic`` is used for
duration measurement (durations are not expected to be reproducible).
"""
from __future__ import annotations

import datetime
import time


def iso_now() -> str:
    """Current local time as an ISO-8601 string with offset."""
    return datetime.datetime.now().astimezone().isoformat()


def fixed_clock(value: str):
    """Return a clock callable that always yields ``value`` (for tests)."""
    def _clock() -> str:
        return value

    return _clock


def counting_clock(start: int = 0, step: int = 1):
    """Return a clock yielding deterministic increasing integers as strings."""
    state = {"n": start}

    def _clock() -> str:
        v = state["n"]
        state["n"] += step
        return f"t{v:06d}"

    return _clock


monotonic = time.monotonic
