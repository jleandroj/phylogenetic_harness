"""Task-type registry (audit Q3: "how much code to add a new task type?").

A ``TaskTypeSpec`` declares everything specific to a kind of analysis: which tool
it uses, how to build its argv from parameters, which validators apply, and an
optional hook that produces statistical evidence for the science layer. Adding a
new task type means writing ONE spec and registering it (plus its tool YAML) —
``runner.py`` and ``scheduler.py`` never change, because they already consume the
generic ``Task`` produced by ``build_task``.

    registry = TaskTypeRegistry()
    registry.register(TaskTypeSpec(task_type="fasta_index", tool_id="samtools",
        build_argv=lambda p: ["samtools", "faidx", p["fasta"]],
        validators=["file_exists", "file_nonempty"]))
    task = registry.build_task(registry.get("fasta_index"), task_id=..., run_id=..., ...)
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .tasks import FailurePolicy, ResourceRequest, Task
from .validators import CheckResult


@dataclass
class TaskTypeSpec:
    task_type: str
    tool_id: str
    build_argv: Callable[[dict[str, Any]], list[str]]
    validators: list[str]
    # Optional: given the produced outputs, return statistical-evidence checks for
    # the science layer (e.g. bootstrap support, gene concordance). Keeps analysis
    # logic in the spec, not in the runner.
    statistical_evidence_hook: Callable[[list[str], dict[str, Any]], Sequence[CheckResult]] | None = None
    seed_required: bool = False
    default_resources: ResourceRequest = field(default_factory=ResourceRequest)
    default_failure_policy: FailurePolicy = field(default_factory=FailurePolicy)

    def build_task(
        self,
        *,
        task_id: str,
        run_id: str,
        params: dict[str, Any],
        inputs: list[str],
        outputs_expected: list[str],
        resources: ResourceRequest | None = None,
        failure_policy: FailurePolicy | None = None,
    ) -> Task:
        argv = self.build_argv(params)
        return Task(
            task_id=task_id, run_id=run_id, task_type=self.task_type, tool_id=self.tool_id,
            command_template=" ".join(argv), command_argv=argv,
            inputs=inputs, outputs_expected=outputs_expected, validators=list(self.validators),
            resources=resources or self.default_resources,
            failure_policy=failure_policy or self.default_failure_policy,
            seed_required=self.seed_required, params=params,
        )


class TaskTypeRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, TaskTypeSpec] = {}

    def register(self, spec: TaskTypeSpec) -> TaskTypeSpec:
        self._specs[spec.task_type] = spec
        return spec

    def get(self, task_type: str) -> TaskTypeSpec:
        if task_type not in self._specs:
            raise KeyError(f"unknown task_type {task_type!r}; registered: {sorted(self._specs)}")
        return self._specs[task_type]

    def all(self) -> dict[str, TaskTypeSpec]:
        return dict(self._specs)


# A couple of built-in specs demonstrating the pattern. Adding more is purely
# additive — no change to the runner/scheduler.
def builtin_registry() -> TaskTypeRegistry:
    reg = TaskTypeRegistry()
    reg.register(TaskTypeSpec(
        task_type="file_copy", tool_id="cp",
        build_argv=lambda p: ["cp", p["src"], p["dst"]],
        validators=["file_exists", "file_nonempty"],
    ))
    reg.register(TaskTypeSpec(
        task_type="fasta_index", tool_id="samtools",
        build_argv=lambda p: ["samtools", "faidx", p["fasta"]],
        validators=["file_exists", "file_nonempty"],
    ))
    return reg
