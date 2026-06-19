"""Task contract (spec §24.3).

A task does not exist unless it has inputs, expected outputs, a tool, resources
and validators. The dataclass enforces those at construction. Technical and
scientific states are tracked separately (spec §9) and only mutate through the
state-machine guard in ``harness.states``.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any

from .states import (
    ScientificState,
    TechnicalState,
    assert_transition,
    default_scientific_state,
)


@dataclass
class ResourceRequest:
    cpus: int = 1
    memory_gb: float = 4.0
    gpu: bool = False
    walltime_minutes: int = 30

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpus": self.cpus,
            "memory_gb": self.memory_gb,
            "gpu": self.gpu,
            "walltime_minutes": self.walltime_minutes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ResourceRequest:
        return cls(
            cpus=d.get("cpus", 1), memory_gb=d.get("memory_gb", 4.0),
            gpu=bool(d.get("gpu", False)), walltime_minutes=d.get("walltime_minutes", 30),
        )


@dataclass
class FailurePolicy:
    retryable: bool = True
    max_retries: int = 2
    timeout_seconds: int = 1800

    def to_dict(self) -> dict[str, Any]:
        return {
            "retryable": self.retryable,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FailurePolicy:
        return cls(
            retryable=bool(d.get("retryable", True)), max_retries=d.get("max_retries", 2),
            timeout_seconds=d.get("timeout_seconds", 1800),
        )


@dataclass
class Task:
    task_id: str
    run_id: str
    task_type: str
    tool_id: str
    command_template: str
    inputs: list[str]
    outputs_expected: list[str]
    validators: list[str]
    resources: ResourceRequest = field(default_factory=ResourceRequest)
    failure_policy: FailurePolicy = field(default_factory=FailurePolicy)
    requires_approval: bool = False
    seed_required: bool = False
    params: dict[str, Any] = field(default_factory=dict)
    # Canonical argv (audit P0.2). When empty it is derived from command_template
    # via shlex.split + per-element formatting, which keeps single-string
    # templates usable while guaranteeing param values stay single argv elements.
    command_argv: list[str] = field(default_factory=list)
    # Mutable state.
    status_technical: TechnicalState = TechnicalState.PENDING
    status_scientific: ScientificState = field(default_factory=default_scientific_state)
    retries: int = 0

    def __post_init__(self) -> None:
        problems = []
        if not self.inputs:
            problems.append("inputs")
        if not self.outputs_expected:
            problems.append("outputs_expected")
        if not self.tool_id:
            problems.append("tool_id")
        if not self.validators:
            problems.append("validators")
        if problems:
            raise ValueError(
                f"task {self.task_id!r} is incomplete; missing: {problems} "
                f"(spec §24.3: a task does not exist without inputs, outputs, tool, validators)"
            )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Task:
        """Reconstruct a Task from its serialised form (for replay/resume/scheduler).

        Mutable run-state (status_*/retries) is intentionally reset to its initial
        value: a reconstructed task is re-run from a clean technical state.
        """
        return cls(
            task_id=d["task_id"], run_id=d["run_id"], task_type=d["task_type"],
            tool_id=d["tool_id"], command_template=d.get("command_template", ""),
            inputs=list(d["inputs"]), outputs_expected=list(d["outputs_expected"]),
            validators=list(d["validators"]),
            resources=ResourceRequest.from_dict(d.get("resources", {})),
            failure_policy=FailurePolicy.from_dict(d.get("failure_policy", {})),
            requires_approval=bool(d.get("requires_approval", False)),
            seed_required=bool(d.get("seed_required", False)),
            params=dict(d.get("params", {})),
            command_argv=list(d.get("command_argv", [])),
        )

    def set_technical(self, new: TechnicalState) -> TechnicalState:
        """Transition technical state through the legal-transition guard."""
        assert_transition(self.status_technical, new)
        self.status_technical = new
        return new

    def set_scientific(self, new: ScientificState) -> ScientificState:
        """Scientific state is assigned by the science layer with evidence.

        Deliberately NOT derived from technical state (spec §9): there is no path
        here that turns SUCCEEDED into SUPPORTED.
        """
        self.status_scientific = new
        return new

    def render_command(self, **bindings: Any) -> str:
        """Human-readable command string. For DISPLAY/logs only — never executed."""
        ctx = {**self.params, **bindings}
        return self.command_template.format(**ctx)

    def render_argv(self, **bindings: Any) -> list[str]:
        """Build the argv list for execution (audit P0.2).

        Each element is formatted INDEPENDENTLY after tokenisation, so a value
        like ``"; rm -rf ~"`` becomes one literal argv element and can never be
        interpreted as shell syntax. No shell is ever involved.
        """
        ctx = {**self.params, **bindings}
        base = self.command_argv or shlex.split(self.command_template)
        return [part.format(**ctx) for part in base]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "task_type": self.task_type,
            "tool_id": self.tool_id,
            "command_template": self.command_template,
            "command_argv": self.command_argv,
            "inputs": self.inputs,
            "outputs_expected": self.outputs_expected,
            "validators": self.validators,
            "resources": self.resources.to_dict(),
            "failure_policy": self.failure_policy.to_dict(),
            "requires_approval": self.requires_approval,
            "seed_required": self.seed_required,
            "params": self.params,
            "status_technical": self.status_technical.value,
            "status_scientific": self.status_scientific.value,
            "retries": self.retries,
        }
