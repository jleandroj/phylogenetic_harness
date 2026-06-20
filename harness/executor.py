"""Command executors (spec §6.1, §24.4, §24.10; audit P0.2/P0.4/P1.6/P1.7/P1.10).

LocalExecutor runs a command as a subprocess with these hard guarantees:

  * ARGV ONLY, never a shell (audit P0.2). ``command`` must be ``list[str]``;
    a string raises. This closes the command-injection hole — a param value can
    never be interpreted as shell syntax.
  * stdout/stderr are streamed to FILES through a byte cap (audit P0.4): output
    beyond the cap is dropped with a ``[TRUNCATED]`` marker and ``truncated_*``
    is set, so a noisy tool cannot exhaust the disk.
  * a pre-flight disk-free check aborts before starting if free space is below a
    threshold (audit P0.4).
  * peak RSS is sampled per child PID (audit P1.6) and logs are named per attempt
    (audit P1.7), so retries never clobber prior evidence.
  * captured output is passed through secret redaction (audit P1.10).

The spec's GPU rules are honoured structurally: CUDA is never initialised in the
parent, the ``spawn`` context is used for any Python child fan-out, and the
assigned GPU is recorded per execution.
"""
from __future__ import annotations

import multiprocessing
import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import clock
from .redaction import redact as _default_redact
from .resources import ChildResourceProbe, PidSampler, ResourceUsage

SPAWN_CONTEXT = multiprocessing.get_context("spawn")

DEFAULT_OUTPUT_CAP_BYTES = 10 * 1024 * 1024      # 10 MiB per stream
DEFAULT_MIN_FREE_BYTES = 100 * 1024 * 1024       # refuse to start under 100 MiB free
_CHUNK = 65536


class ShellCommandRejected(TypeError):
    """Raised when a string command is passed (shell execution is forbidden)."""


def _emit_line(out, line: bytes, written: int, cap: int, truncated: bool,
               redactor: Callable[[str], str]) -> tuple[int, bool]:
    if truncated or written >= cap:
        return written, True
    text = line.decode("utf-8", errors="replace")
    data = redactor(text).encode("utf-8", errors="replace")
    out.write(data)
    return written + len(data), False


def _pump(src, dst_path: Path, cap: int, redactor: Callable[[str], str]) -> bool:
    """Drain ``src`` into ``dst_path`` line by line up to ``cap`` bytes.

    Always fully drains the pipe (so the child never blocks), but stops writing
    after the cap. Memory is bounded by the cap plus one partial line.
    """
    written = 0
    truncated = False
    buf = b""
    with open(dst_path, "wb") as out:
        while True:
            chunk = src.read(_CHUNK)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                written, truncated = _emit_line(out, line + b"\n", written, cap, truncated, redactor)
            if len(buf) >= _CHUNK:  # very long line with no newline yet
                written, truncated = _emit_line(out, buf, written, cap, truncated, redactor)
                buf = b""
        if buf:
            written, truncated = _emit_line(out, buf, written, cap, truncated, redactor)
        if truncated:
            out.write(f"\n[TRUNCATED at {cap} bytes]\n".encode())
    return truncated


@dataclass
class ExecutionResult:
    task_id: str
    command: list[str]
    exit_code: int | None
    started_at: str | None
    finished_at: str | None
    wall_seconds: float | None
    stdout_path: str | None
    stderr_path: str | None
    timed_out: bool = False
    truncated_stdout: bool = False
    truncated_stderr: bool = False
    disk_aborted: bool = False
    resources: ResourceUsage | None = None
    gpu_assigned: str | None = None
    cwd: str | None = None
    pid: int | None = None
    attempt: int = 1
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.exit_code == 0
            and not self.timed_out
            and not self.disk_aborted
            and self.error is None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "command": list(self.command),
            "command_display": " ".join(self.command),
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "wall_seconds": self.wall_seconds,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "timed_out": self.timed_out,
            "truncated_stdout": self.truncated_stdout,
            "truncated_stderr": self.truncated_stderr,
            "disk_aborted": self.disk_aborted,
            "gpu_assigned": self.gpu_assigned,
            "cwd": self.cwd,
            "pid": self.pid,
            "attempt": self.attempt,
            "error": self.error,
            "succeeded": self.succeeded,
            "resources": self.resources.to_dict() if self.resources else None,
        }


def _require_argv(command: Any) -> list[str]:
    if isinstance(command, str):
        raise ShellCommandRejected(
            "LocalExecutor requires an argv list, not a string (shell execution is forbidden; "
            "build a list[str] so params cannot be interpreted as shell syntax)"
        )
    if not isinstance(command, list) or not all(isinstance(p, str) for p in command):
        raise ShellCommandRejected("command must be a list[str] (argv)")
    if not command:
        raise ShellCommandRejected("command argv is empty")
    return command


class LocalExecutor:
    name = "local"

    def __init__(
        self,
        log_dir: str | os.PathLike[str],
        *,
        clock_fn=clock.iso_now,
        disk_path: str | os.PathLike[str] = ".",
        output_cap_bytes: int = DEFAULT_OUTPUT_CAP_BYTES,
        min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
        redactor: Callable[[str], str] = _default_redact,
        sandbox: dict[str, Any] | None = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock_fn
        self.disk_path = disk_path
        self.output_cap_bytes = output_cap_bytes
        self.min_free_bytes = min_free_bytes
        self.redactor = redactor
        # Optional sandbox: {"enabled": bool, "backend": "auto"|"bwrap"|"apptainer",
        # "binds": [writable dirs], "image": <apptainer image>}. When enabled the
        # argv is wrapped (no network, fresh /tmp, read-only root) before execution.
        self.sandbox = sandbox or {"enabled": False}
        self._sandbox_mode = "disabled"

    def _log_paths(self, task_id: str, attempt: int) -> tuple[Path, Path]:
        stem = f"{task_id}.attempt{attempt}"
        return self.log_dir / f"{stem}.stdout.log", self.log_dir / f"{stem}.stderr.log"

    def run(
        self,
        task_id: str,
        command: list[str],
        *,
        timeout_seconds: int | None = None,
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        gpu_assigned: str | None = None,
        attempt: int = 1,
        stdout_to: str | os.PathLike[str] | None = None,
    ) -> ExecutionResult:
        argv = _require_argv(command)
        # Optionally wrap the command in a sandbox (audit round 4 #3 -> default in
        # the pipeline). The output dir must be a writable bind so the tool can
        # write its results; everything else is read-only / no network / fresh /tmp.
        if self.sandbox.get("enabled"):
            from .sandbox import wrap
            argv, self._sandbox_mode = wrap(
                argv, enabled=True, backend=self.sandbox.get("backend", "auto"),
                image=self.sandbox.get("image"), binds=self.sandbox.get("binds"),
                allow_net=self.sandbox.get("allow_net", False),
            )
        stderr_log = self._log_paths(task_id, attempt)[1]
        # When a tool writes its result to stdout (mafft, fasttree, ...), capture it
        # FAITHFULLY to the declared output file — no shell redirection, no cap, no
        # redaction (it is a scientific output, not a log). stderr still goes to the
        # capped/redacted log. Otherwise stdout goes to the normal capped log.
        faithful_stdout = stdout_to is not None
        stdout_path = Path(stdout_to) if stdout_to is not None else self._log_paths(task_id, attempt)[0]
        stderr_path = stderr_log

        # Pre-flight disk check (audit P0.4): refuse to start if nearly full.
        import shutil
        try:
            free = shutil.disk_usage(self.disk_path).free
        except OSError:
            free = None
        if free is not None and free < self.min_free_bytes:
            ts = self._clock()
            return ExecutionResult(
                task_id=task_id, command=argv, exit_code=None, started_at=ts, finished_at=ts,
                wall_seconds=0.0, stdout_path=None, stderr_path=None, disk_aborted=True,
                attempt=attempt, error=f"disk_abort: {free} bytes free < {self.min_free_bytes} threshold",
            )

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
        truncated_out = truncated_err = False
        sampler: PidSampler | None = None
        out_fh = None
        try:
            if faithful_stdout:
                stdout_path.parent.mkdir(parents=True, exist_ok=True)
                out_fh = open(stdout_path, "wb")
                stdout_target: Any = out_fh
            else:
                stdout_target = subprocess.PIPE
            proc = subprocess.Popen(
                argv, shell=False, stdout=stdout_target, stderr=subprocess.PIPE,
                cwd=str(cwd) if cwd else None, env=child_env, bufsize=0,
            )
            pid = proc.pid
            sampler = PidSampler(pid)
            sampler.start()
            holder: dict[str, bool] = {}
            t_out = None
            if not faithful_stdout:
                t_out = threading.Thread(
                    target=lambda: holder.__setitem__(
                        "out", _pump(proc.stdout, stdout_path, self.output_cap_bytes, self.redactor)))
                t_out.start()
            t_err = threading.Thread(
                target=lambda: holder.__setitem__(
                    "err", _pump(proc.stderr, stderr_path, self.output_cap_bytes, self.redactor)))
            t_err.start()
            try:
                exit_code = proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                exit_code = proc.wait()
            if t_out is not None:
                t_out.join()
            t_err.join()
            truncated_out = holder.get("out", False)
            truncated_err = holder.get("err", False)
        except (OSError, ValueError) as exc:
            error = str(exc)
        finally:
            if out_fh is not None:
                out_fh.close()
            if sampler is not None:
                sampler.stop()
                sampler.join(timeout=1.0)

        wall = clock.monotonic() - t0
        usage = probe.stop(wall_seconds=wall, sampler=sampler)
        finished_at = self._clock()

        return ExecutionResult(
            task_id=task_id, command=argv, exit_code=exit_code, started_at=started_at,
            finished_at=finished_at, wall_seconds=round(wall, 4), stdout_path=str(stdout_path),
            stderr_path=str(stderr_path), timed_out=timed_out, truncated_stdout=truncated_out,
            truncated_stderr=truncated_err, resources=usage, gpu_assigned=gpu_assigned,
            cwd=str(cwd) if cwd else str(Path.cwd()), pid=pid, attempt=attempt, error=error,
        )


class _NonExecutingExecutor:
    """Base for executors that record intent without running anything."""

    name = "base"
    _why = ""

    def __init__(self, log_dir: str | os.PathLike[str], *, clock_fn=clock.iso_now, **_: Any) -> None:
        self.log_dir = Path(log_dir)
        self._clock = clock_fn

    def run(self, task_id: str, command: list[str], *, attempt: int = 1, **_: Any) -> ExecutionResult:
        argv = command if isinstance(command, list) else [str(command)]
        ts = self._clock()
        return ExecutionResult(
            task_id=task_id, command=argv, exit_code=None, started_at=ts, finished_at=ts,
            wall_seconds=0.0, stdout_path=None, stderr_path=None, attempt=attempt,
            error=f"{self.name}: {self._why}",
        )


class DryRunExecutor(_NonExecutingExecutor):
    name = "dry_run"
    _why = "command not executed"


class AuditOnlyExecutor(_NonExecutingExecutor):
    name = "audit_only"
    _why = "execution disabled"


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
    if cls in (DryRunExecutor, AuditOnlyExecutor):
        return cls(log_dir, clock_fn=kw.get("clock_fn", clock.iso_now))
    return cls(log_dir, **kw)
