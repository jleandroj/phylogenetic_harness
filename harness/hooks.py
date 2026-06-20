"""Lifecycle hooks (audit round 4 #1).

A genuine, bounded extension point — NOT an LLM-agent layer. Hooks fire at three
points in a task's lifecycle: before execution, after a successful run, and on
error. Each hook is ERROR-ISOLATED: a hook that raises is caught, recorded via a
``hook_failed`` event, and never breaks the run (a hook must not be able to take
down the pipeline). The reserved ``hook_*`` events are now actually emitted, so
"what hook ran and what happened" is auditable.

    hooks = HookRegistry(events=run.events)
    hooks.on_pre_task(lambda task: ...)        # e.g. enforce a policy, stamp metadata
    hooks.on_post_task(lambda task, bundle: ...)  # e.g. push a metric, notify
    hooks.on_error(lambda task, exc: ...)      # e.g. alert
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .events import EventStore, EventType


def _name(fn: Callable[..., Any]) -> str:
    return getattr(fn, "__name__", None) or getattr(fn, "__qualname__", None) or repr(fn)


@dataclass
class HookRegistry:
    events: EventStore | None = None
    _pre: list[Callable[..., Any]] = field(default_factory=list)
    _post: list[Callable[..., Any]] = field(default_factory=list)
    _error: list[Callable[..., Any]] = field(default_factory=list)

    def on_pre_task(self, fn: Callable[[Any], Any]) -> Callable[[Any], Any]:
        self._pre.append(fn)
        return fn

    def on_post_task(self, fn: Callable[[Any, dict[str, Any]], Any]) -> Callable[..., Any]:
        self._post.append(fn)
        return fn

    def on_error(self, fn: Callable[[Any, BaseException], Any]) -> Callable[..., Any]:
        self._error.append(fn)
        return fn

    def _fire(self, hooks: list[Callable[..., Any]], phase: str, task_id: str, *args: Any) -> None:
        for fn in hooks:
            hook = _name(fn)
            if self.events:
                self.events.emit(EventType.HOOK_FIRED, hook=hook, phase=phase, task_id=task_id)
            try:
                fn(*args)
            except Exception as exc:  # error-isolated: a bad hook never breaks the run
                if self.events:
                    self.events.emit(EventType.HOOK_FAILED, hook=hook, phase=phase,
                                     task_id=task_id, error=f"{type(exc).__name__}: {exc}")
            else:
                if self.events:
                    self.events.emit(EventType.HOOK_SUCCEEDED, hook=hook, phase=phase, task_id=task_id)

    def fire_pre(self, task: Any) -> None:
        self._fire(self._pre, "pre_task", task.task_id, task)

    def fire_post(self, task: Any, bundle: dict[str, Any]) -> None:
        self._fire(self._post, "post_task", task.task_id, task, bundle)

    def fire_error(self, task: Any, exc: BaseException) -> None:
        self._fire(self._error, "on_error", task.task_id, task, exc)
