"""TaskRunner — the single, enforced execution path (audit P0.1).

Nothing may execute a command except through ``TaskRunner.run_task``. It forces,
in order and with no way to skip a step:

  1. approval gate            (ApprovalError if a flagged task lacks a grant)
  2. tool registry gate       (refuses unregistered / unavailable tools)
  3. APPROVED -> LEASED       (via LeaseManager)
  4. LEASED   -> RUNNING      (+ task_started / command_started events)
  5. argv-only execution      (LocalExecutor; no shell; output capped)
  6. run ALL declared validators over outputs_expected
  7. build the scientific Interpretation (science layer)
  8. terminal technical state: SUCCEEDED only if exit==0 AND every validator
     passed AND not timed out / disk-aborted; otherwise FAILED_RETRYABLE or
     FAILED_FATAL per the failure policy + retry budget
  9. persist results/{task_id}.validation.json
 10. emit the matching events at every step

The scientific state is assigned ONLY by the science layer — never derived from
a green exit code (the project's core invariant).
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import science
from .events import EventStore, EventType
from .executor import ExecutionResult
from .leases import LeaseManager
from .seeds import SeedManager
from .states import TechnicalState
from .tasks import Task
from .tools import ToolRegistry
from .validators import CheckResult, ValidatorRegistry


class TaskRunner:
    def __init__(
        self,
        *,
        events: EventStore,
        tools: ToolRegistry,
        validators: ValidatorRegistry,
        approval,
        executor,
        leases: LeaseManager,
        results_dir: str | Path,
        seeds: SeedManager | None = None,
        worker_id: str = "worker-0",
        clock_fn=None,
        hooks=None,
    ) -> None:
        self.events = events
        self.tools = tools
        self.validators = validators
        self.approval = approval
        self.executor = executor
        self.leases = leases
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.seeds = seeds
        self.worker_id = worker_id
        self._clock = clock_fn or (lambda: 0)
        # Lifecycle hooks (audit round 4 #1). Default: an empty, no-op registry.
        if hooks is None:
            from .hooks import HookRegistry
            hooks = HookRegistry(events=events)
        self.hooks = hooks

    def _now(self) -> Any:
        return self._clock()

    def _persist_bundle(self, task_id: str, bundle: dict[str, Any]) -> None:
        # P3.10: explicit serialisation. A non-serialisable value is a bug we want
        # to surface loudly, not silently stringify with default=str.
        text = json.dumps(bundle, indent=2, sort_keys=True)
        (self.results_dir / f"{task_id}.validation.json").write_text(text, encoding="utf-8")

    def _to_failed(self, task: Task, reason: str, *, fatal: bool) -> TechnicalState:
        """Transition a RUNNING task to a failed state and emit task_failed."""
        new = TechnicalState.FAILED_FATAL if fatal else TechnicalState.FAILED_RETRYABLE
        task.set_technical(new)
        self.events.emit(EventType.TASK_FAILED, task_id=task.task_id, reason=reason, state=new.value)
        return new

    def _build_argv(self, task: Task, contract, bindings: dict[str, Any]) -> list[str]:
        # P1.9: deterministic seed injection.
        if task.seed_required and self.seeds is not None:
            bindings.setdefault("seed", self.seeds.derive(task.run_id, task.task_id))
        argv = task.render_argv(**bindings)
        if task.seed_required and contract.seed_flag and self.seeds is not None:
            argv = argv + [contract.seed_flag, str(bindings["seed"])]
        return argv

    def _gpu_for(self, task: Task) -> str | None:
        """Resolve the GPU to pin (P2.9): only when the task wants GPU AND a real
        device is visible. Never blindly assume device 0."""
        if not task.resources.gpu:
            return None
        from . import hardware
        gpu = hardware.gpu_profile()
        if gpu.get("available") and gpu.get("devices"):
            return str(gpu["devices"][0]["index"])
        return None  # GPU requested but none usable; executor records it unset

    def _attempt(
        self, task: Task, argv: list[str], attempt: int, validator_kwargs: dict[str, Any]
    ) -> tuple[ExecutionResult, list[CheckResult], bool]:
        """One execution attempt: run + validate. Raises propagate to run_task."""
        self.events.emit(EventType.TASK_STARTED, task_id=task.task_id, attempt=attempt)
        self.events.emit(EventType.COMMAND_STARTED, task_id=task.task_id, command=argv)
        result = self.executor.run(
            task.task_id, argv,
            timeout_seconds=task.failure_policy.timeout_seconds,
            gpu_assigned=self._gpu_for(task),
            attempt=attempt,
            # A tool that writes its result to stdout declares the target here, so
            # the output is captured faithfully without any shell redirection.
            stdout_to=task.params.get("stdout_to"),
        )
        self.events.emit(
            EventType.COMMAND_FINISHED, task_id=task.task_id,
            exit_code=result.exit_code, timed_out=result.timed_out,
            disk_aborted=result.disk_aborted,
            truncated=result.truncated_stdout or result.truncated_stderr,
        )
        self.events.emit(EventType.VALIDATION_STARTED, task_id=task.task_id)
        checks: list[CheckResult] = []
        for out in task.outputs_expected:
            for vname in task.validators:
                checks.append(self.validators.run(vname, out, **validator_kwargs))
        all_passed = bool(checks) and all(c.passed for c in checks)
        self.events.emit(
            EventType.VALIDATION_SUCCEEDED if all_passed else EventType.VALIDATION_FAILED,
            task_id=task.task_id, checks=[c.to_dict() for c in checks],
        )
        return result, checks, all_passed

    def _auto_degeneracy(
        self, task: Task, degeneracy: science.DegeneracyReport | None
    ) -> science.DegeneracyReport:
        """Q4: a SUCCEEDED-but-degenerate output must never read as clean. If the
        caller did not supply a degeneracy report, derive one from the outputs
        (empty file / only gaps-N)."""
        if degeneracy is not None:
            return degeneracy
        reasons: list[str] = []
        for out in task.outputs_expected:
            p = Path(out)
            if p.exists() and p.is_file():
                if p.stat().st_size == 0:
                    reasons.append(f"output {p.name} is empty")
                else:
                    body = "".join(
                        ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()
                        if not ln.startswith(">")
                    )
                    if body and set(body.upper()) <= {"N", "-", ".", "\n"}:
                        reasons.append(f"output {p.name} is only gaps/N")
        return science.DegeneracyReport(degenerate=bool(reasons), reasons=reasons)

    def run_task(
        self,
        task: Task,
        *,
        bindings: dict[str, Any] | None = None,
        validator_kwargs: dict[str, Any] | None = None,
        statistical_checks: Sequence[CheckResult] | None = None,
        statistical_evidence_hook: Any | None = None,
        negative: science.NegativeResult | None = None,
        degeneracy: science.DegeneracyReport | None = None,
        allowed: Sequence[str] | None = None,
        limitations: Sequence[str] | None = None,
        extra_not_allowed: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        bindings = dict(bindings or {})
        validator_kwargs = dict(validator_kwargs or {})

        self.events.emit(EventType.TASK_CREATED, task_id=task.task_id, tool_id=task.tool_id)

        # Gates run BEFORE any lease/state change; their exceptions propagate as
        # before (no lease to leak yet).
        self.approval.check(task)                                  # ApprovalError
        contract = self.tools.require_runnable(task.tool_id)       # Unregistered/ToolUnavailable

        task.set_technical(TechnicalState.APPROVED)
        self.events.emit(EventType.TASK_APPROVED, task_id=task.task_id)
        self.hooks.fire_pre(task)  # pre-task hooks (error-isolated)
        argv = self._build_argv(task, contract, bindings)

        result: ExecutionResult | None = None
        checks: list[CheckResult] = []
        all_passed = False
        final = TechnicalState.FAILED_FATAL
        policy = task.failure_policy

        # P0.2 retry loop. P0.1 exception boundary wraps every attempt: any raise
        # emits task_failed, releases the lease and leaves a TERMINAL state.
        while True:
            self.leases.acquire(task, self.worker_id, now=self._now())  # ->LEASED
            try:
                task.set_technical(TechnicalState.RUNNING)
                result, checks, all_passed = self._attempt(
                    task, argv, task.retries + 1, validator_kwargs
                )
                exec_ok = result.succeeded
                if exec_ok and all_passed:
                    task.set_technical(TechnicalState.SUCCEEDED)
                    self.events.emit(EventType.TASK_SUCCEEDED, task_id=task.task_id)
                    final = TechnicalState.SUCCEEDED
                    break
                # Failure path.
                if not exec_ok:
                    reason = (
                        "disk_abort" if result.disk_aborted
                        else "timeout" if result.timed_out
                        else f"exit_code={result.exit_code}"
                    )
                    fatal = result.disk_aborted
                else:
                    reason = f"validators_failed={[c.name for c in checks if not c.passed]}"
                    fatal = False
                can_retry = (
                    not fatal and policy.retryable and task.retries < policy.max_retries
                )
                self._to_failed(task, reason, fatal=not can_retry)
                if not can_retry:
                    final = TechnicalState.FAILED_FATAL
                    break
                # Retryable: FAILED_RETRYABLE -> REQUEUED, then loop re-leases.
                task.retries += 1
                task.set_technical(TechnicalState.REQUEUED)
                self.events.emit(EventType.TASK_REQUEUED, task_id=task.task_id, attempt=task.retries)
            except Exception as exc:  # P0.1: no exception escapes with a live lease
                final = self._to_failed(task, f"exception:{type(exc).__name__}:{exc}", fatal=True)
                self.events.emit(
                    EventType.RESULT_INTERPRETATION_LIMITED, task_id=task.task_id,
                    reason="execution raised; not biologically interpretable",
                )
                self.hooks.fire_error(task, exc)  # on-error hooks (error-isolated)
                break
            finally:
                self.leases.release(task.task_id)

        # Real statistical evidence (audit: feed the science layer). A task type's
        # hook computes evidence from the produced outputs (e.g. alignment quality,
        # tree support) only when the task actually succeeded.
        stat_checks = list(statistical_checks or [])
        if final == TechnicalState.SUCCEEDED and statistical_evidence_hook is not None:
            produced = [o for o in task.outputs_expected if Path(o).exists()]
            try:
                stat_checks.extend(statistical_evidence_hook(task, produced))
            except Exception as exc:  # evidence is best-effort; never crash the run
                self.events.emit(
                    EventType.RESULT_INTERPRETATION_LIMITED, task_id=task.task_id,
                    reason=f"evidence_hook_failed:{type(exc).__name__}:{exc}",
                )

        # Scientific interpretation (only the science layer sets sci state). Q4:
        # auto-detect degeneracy so a clean-looking but degenerate output can't lie.
        degen = self._auto_degeneracy(task, degeneracy) if final == TechnicalState.SUCCEEDED else degeneracy
        interp = science.build_interpretation(
            checks, statistical_checks=stat_checks, degeneracy=degen,
            negative=negative, allowed=allowed, limitations=limitations,
            extra_not_allowed=extra_not_allowed,
        )
        task.set_scientific(interp.scientific_state)

        # Checksum the produced outputs so downstream aggregation/diff is provenance-
        # complete (Q4/Q5) and a result can be compared byte-for-byte across runs.
        from . import ids
        outputs: list[dict[str, Any]] = []
        for out in task.outputs_expected:
            p = Path(out)
            if p.exists() and p.is_file():
                outputs.append({"path": str(p), "sha256": "sha256:" + ids.sha256_file(p)})
            else:
                outputs.append({"path": str(out), "sha256": None})

        bundle = {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "tool_id": task.tool_id,
            "status_technical": final.value,
            "status_scientific": task.status_scientific.value,
            "degenerate": bool(degen.degenerate) if degen is not None else False,
            "validators_passed": all_passed,
            "retries": task.retries,
            "outputs": outputs,
            "execution": result.to_dict() if result is not None else None,
            "validation": [c.to_dict() for c in checks],
            "interpretation": interp.to_dict(),
        }
        self._persist_bundle(task.task_id, bundle)
        self.hooks.fire_post(task, bundle)  # post-task hooks (error-isolated)
        return bundle
