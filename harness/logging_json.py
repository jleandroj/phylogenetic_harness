"""Structured JSON logger (spec §8).

The spec forbids ``print()`` as the primary logging mechanism. Every log line is
a single JSON object written to a file (and optionally mirrored to stderr), so
logs are machine-parseable and auditable after the fact.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO


class JsonLogger:
    """Append-only JSON-lines logger. Thread-safe for concurrent workers."""

    LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        mirror_stderr: bool = False,
        context: dict[str, Any] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._fh: TextIO | None = None
        if path is not None:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(p, "a", encoding="utf-8")
        self._mirror = mirror_stderr
        self._context = dict(context or {})
        # clock() must return an ISO timestamp string. Injected so tests/cron
        # stay deterministic and the module never calls a forbidden implicit clock.
        self._clock = clock

    def bind(self, **context: Any) -> JsonLogger:
        """Return a child logger sharing the same sink with extra context."""
        child = JsonLogger.__new__(JsonLogger)
        child._lock = self._lock
        child._fh = self._fh
        child._mirror = self._mirror
        child._context = {**self._context, **context}
        child._clock = self._clock
        return child

    def log(self, level: str, message: str, **fields: Any) -> dict[str, Any]:
        level = level.upper()
        record: dict[str, Any] = {"level": level, "message": message}
        if self._clock is not None:
            record["ts"] = self._clock()
        record.update(self._context)
        record.update(fields)
        line = json.dumps(record, sort_keys=True, default=str)
        with self._lock:
            if self._fh is not None:
                self._fh.write(line + "\n")
                self._fh.flush()
            if self._mirror:
                sys.stderr.write(line + "\n")
        return record

    def debug(self, message: str, **f: Any) -> dict[str, Any]:
        return self.log("DEBUG", message, **f)

    def info(self, message: str, **f: Any) -> dict[str, Any]:
        return self.log("INFO", message, **f)

    def warning(self, message: str, **f: Any) -> dict[str, Any]:
        return self.log("WARNING", message, **f)

    def error(self, message: str, **f: Any) -> dict[str, Any]:
        return self.log("ERROR", message, **f)

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
