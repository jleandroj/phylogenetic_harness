"""Command executors (spec §6.1, §24.4, §24.10).

LocalExecutor runs a command as a subprocess, capturing stdout and stderr to
FILES (not just memory), exit code, wall time and child resource usage. The
spec's GPU rules are honoured structurally: we never import CUDA in the parent,
we use the ``spawn`` multiprocessing context for any Python child fan-out, and
the assigned GPU is recorded per execution.

DryRunExecutor records the intended command without running it.
AuditOnlyExecutor refuses to execute and only verifies the task is auditable.
SLURMExecutor / GPUExecutor are declared interfaces, intentionally not
implemented in v1 (they raise NotImplementedError).
"""
from __future__ import annotations

import multiprocessing
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import clock
from .resources import ChildResourceProbe, ResourceUsage

# A spawn context is the safe default when CUDA may be involved downstream
# (spec §24.10: no fork with CUDA, do not init CUDA in the parent). We expose it
# so any Python child fan-out uses spawn, never the default fork on Linux.
SPAWN_CONTEXT = multiprocessing.get_context("spawn")


@dataclass
class ExecutionResult:
    task_id: str
    command: str
    exit_code: int | None
    started_at: str | None
    finished_at: str | None
    wall_seconds: float | None
    stdout_path: str | None
    stderr_path: str | None
    timed_out: bool = False
    resources: ResourceUsage | None = None
    gpu_assigned: str | None = None
    cwd: str | None = None
    pid: int | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "command": self.command,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "wall_seconds": self.wall_seconds,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "timed_out": self.timed_out,
            "gpu_assigned": self.gpu_assigned,
            "cwd": self.cwd,
            "pid": self.pid,
            "error": self.error,
            "succeeded": self.succeeded,
            "resources": self.resources.to_dict() if self.resources else None,
        }


class LocalExecutor:
    name = "local"

    def __init__(
        self,
        log_dir: str | os.PathLike[str],
        *,
        clock_fn=clock.iso_now,
        disk_path: str | os.PathLike[str] = ".",
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock_fn
        self.disk_path = disk_path

    def run(
        self,
        task_id: str,
        command: str | list[str],
        *,
        timeout_seconds: int | None = None,
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        gpu_assigned: str | None = None,
    ) -> ExecutionResult:
        stdout_path = self.log_dir / f"{task_id}.stdout.log"
        stderr_path = self.log_dir / f"{task_id}.stderr.log"
        cmd_str = command if isinstance(command, str) else " ".join(command)

        # Build child env: never initialise CUDA in the parent; pin the assigned
        # GPU for the child via CUDA_VISIBLE_DEVICES (spec §24.10).
        child_env = dict(os.environ if env is None else env)
        if gpu_assigned is not None:
            child_env["CUDA_VISIBLE_DEVICES"] = str(gpu_assigned)

        probe = ChildResourceProbe(self.disk_path)
        started_at = self._clock()
        t0 = clock.monotonic()
        probe.start()

        timed_out = False
        error = None
        exit_code: int | None = None
        pid: int | None = None
        try:
            with open(stdout_path, "w", encoding="utf-8") as out, open(
                stderr_path, "w", encoding="utf-8"
            ) as err:
                proc = subprocess.Popen(
                    command,
                    shell=isinstance(command, str),
                    stdout=out,
                    stderr=err,
                    cwd=str(cwd) if cwd else None,
                    env=child_env,
                )
                pid = proc.pid
                try:
                    exit_code = proc.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    proc.kill()
                    proc.wait()
                    exit_code = proc.returncode
        except (OSError, ValueError) as exc:
            error = str(exc)

        wall = clock.monotonic() - t0
        usage = probe.stop(wall_seconds=wall)
        finished_at = self._clock()

        return ExecutionResult(
            task_id=task_id,
            command=cmd_str,
            exit_code=exit_code,
            started_at=started_at,
            finished_at=finished_at,
            wall_seconds=round(wall, 4),
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            timed_out=timed_out,
            resources=usage,
            gpu_assigned=gpu_assigned,
            cwd=str(cwd) if cwd else str(Path.cwd()),
            pid=pid,
            error=error,
        )


class DryRunExecutor:
    name = "dry_run"

    def __init__(self, log_dir: str | os.PathLike[str], *, clock_fn=clock.iso_now) -> None:
        self.log_dir = Path(log_dir)
        self._clock = clock_fn

    def run(self, task_id: str, command: str | list[str], **_: Any) -> ExecutionResult:
        cmd_str = command if isinstance(command, str) else " ".join(command)
        ts = self._clock()
        return ExecutionResult(
            task_id=task_id,
            command=cmd_str,
            exit_code=None,
            started_at=ts,
            finished_at=ts,
            wall_seconds=0.0,
            stdout_path=None,
            stderr_path=None,
            error="dry_run: command not executed",
        )


class AuditOnlyExecutor:
    name = "audit_only"

    def __init__(self, log_dir: str | os.PathLike[str], *, clock_fn=clock.iso_now) -> None:
        self.log_dir = Path(log_dir)
        self._clock = clock_fn

    def run(self, task_id: str, command: str | list[str], **_: Any) -> ExecutionResult:
        cmd_str = command if isinstance(command, str) else " ".join(command)
        ts = self._clock()
        return ExecutionResult(
            task_id=task_id,
            command=cmd_str,
            exit_code=None,
            started_at=ts,
            finished_at=ts,
            wall_seconds=0.0,
            stdout_path=None,
            stderr_path=None,
            error="audit_only: execution disabled",
        )


class SLURMExecutor:
    """Declared interface for HPC submission (spec §6.3). Not implemented in v1."""

    name = "slurm"

    def run(self, *_: Any, **__: Any) -> ExecutionResult:
        raise NotImplementedError("SLURMExecutor is a v1 stub; not implemented yet")


class GPUExecutor:
    """Declared interface for GPU-pinned execution (spec §6.1). Not implemented in v1."""

    name = "gpu"

    def run(self, *_: Any, **__: Any) -> ExecutionResult:
        raise NotImplementedError("GPUExecutor is a v1 stub; not implemented yet")


def get_executor(mode: str, log_dir: str | os.PathLike[str], **kw: Any):
    table = {
        "local": LocalExecutor,
        "dry_run": DryRunExecutor,
        "audit_only": AuditOnlyExecutor,
        "slurm": SLURMExecutor,
        "gpu": GPUExecutor,
    }
    if mode not in table:
        raise ValueError(f"unknown executor mode {mode!r}; choices: {sorted(table)}")
    cls = table[mode]
    if cls in (SLURMExecutor, GPUExecutor):
        return cls()
    return cls(log_dir, **kw)
