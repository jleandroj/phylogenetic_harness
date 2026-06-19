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

    def _now(self) -> Any:
        return self._clock()

    def _fail(self, task: Task, reason: str, *, fatal: bool = False) -> TechnicalState:
        policy = task.failure_policy
        if fatal or not policy.retryable or task.retries >= policy.max_retries:
            new = TechnicalState.FAILED_FATAL
        else:
            new = TechnicalState.FAILED_RETRYABLE
        task.set_technical(new)
        self.events.emit(EventType.TASK_FAILED, task_id=task.task_id, reason=reason, state=new.value)
        return new

    def run_task(
        self,
        task: Task,
        *,
        bindings: dict[str, Any] | None = None,
        validator_kwargs: dict[str, Any] | None = None,
        statistical_checks: Sequence[CheckResult] | None = None,
        negative: science.NegativeResult | None = None,
        degeneracy: science.DegeneracyReport | None = None,
        allowed: Sequence[str] | None = None,
        limitations: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        bindings = dict(bindings or {})
        validator_kwargs = dict(validator_kwargs or {})

        self.events.emit(EventType.TASK_CREATED, task_id=task.task_id, tool_id=task.tool_id)

        # 1) approval gate (raises ApprovalError).
        self.approval.check(task)

        # 2) tool registry gate (raises Unregistered/ToolUnavailable). BEFORE any
        #    state change or execution — this is the invariant that was missing.
        contract = self.tools.require_runnable(task.tool_id)

        # 1b) PENDING -> APPROVED.
        task.set_technical(TechnicalState.APPROVED)
        self.events.emit(EventType.TASK_APPROVED, task_id=task.task_id)

        # 3) lease: APPROVED -> LEASED.
        self.leases.acquire(task, self.worker_id, now=self._now())

        # P1.9: derive + inject a deterministic seed if the task/tool wants one.
        if task.seed_required and self.seeds is not None:
            seed = self.seeds.derive(task.run_id, task.task_id)
            bindings.setdefault("seed", seed)
            if contract.seed_flag:
                # appended below after argv render
                pass

        argv = task.render_argv(**bindings)
        if task.seed_required and contract.seed_flag and self.seeds is not None:
            argv = argv + [contract.seed_flag, str(bindings["seed"])]

        # 4) LEASED -> RUNNING.
        task.set_technical(TechnicalState.RUNNING)
        self.events.emit(EventType.TASK_STARTED, task_id=task.task_id, attempt=task.retries + 1)
        self.events.emit(EventType.COMMAND_STARTED, task_id=task.task_id, command=argv)

        # 5) execute (argv-only, capped).
        result: ExecutionResult = self.executor.run(
            task.task_id, argv,
            timeout_seconds=task.failure_policy.timeout_seconds,
            gpu_assigned=("0" if task.resources.gpu else None),
            attempt=task.retries + 1,
        )
        self.events.emit(
            EventType.COMMAND_FINISHED, task_id=task.task_id,
            exit_code=result.exit_code, timed_out=result.timed_out,
            disk_aborted=result.disk_aborted,
            truncated=result.truncated_stdout or result.truncated_stderr,
        )

        # 6) run ALL validators over expected outputs.
        self.events.emit(EventType.VALIDATION_STARTED, task_id=task.task_id)
        checks: list[CheckResult] = []
        for out in task.outputs_expected:
            for vname in task.validators:
                checks.append(self.validators.run(vname, out, **validator_kwargs))
        all_passed = bool(checks) and all(c.passed for c in checks)
        self.events.emit(
            EventType.VALIDATION_SUCCEEDED if all_passed else EventType.VALIDATION_FAILED,
            task_id=task.task_id,
            checks=[c.to_dict() for c in checks],
        )

        # 7) scientific interpretation (only the science layer sets sci state).
        interp = science.build_interpretation(
            checks,
            statistical_checks=statistical_checks,
            degeneracy=degeneracy,
            negative=negative,
            allowed=allowed,
            limitations=limitations,
        )
        task.set_scientific(interp.scientific_state)

        # 8) terminal technical state.
        self.leases.release(task.task_id)
        exec_ok = result.succeeded
        if not exec_ok:
            why = (
                "disk_abort" if result.disk_aborted
                else "timeout" if result.timed_out
                else f"exit_code={result.exit_code}"
            )
            final = self._fail(task, why, fatal=result.disk_aborted)
        elif not all_passed:
            failed = [c.name for c in checks if not c.passed]
            final = self._fail(task, f"validators_failed={failed}")
        else:
            task.set_technical(TechnicalState.SUCCEEDED)
            self.events.emit(EventType.TASK_SUCCEEDED, task_id=task.task_id)
            final = TechnicalState.SUCCEEDED

        # 9) persist the result bundle.
        bundle = {
            "task_id": task.task_id,
            "status_technical": final.value,
            "status_scientific": task.status_scientific.value,
            "execution": result.to_dict(),
            "validation": [c.to_dict() for c in checks],
            "interpretation": interp.to_dict(),
        }
        (self.results_dir / f"{task.task_id}.validation.json").write_text(
            json.dumps(bundle, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        return bundle
