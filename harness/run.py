"""Run configuration and orchestration (spec §24.13).

RunConfig is frozen (immutable) — a full pipeline never runs without a frozen
config. The Run object wires together the event store, logger, environment
capture, tool/validator registries, approval gate and executor, and lays out the
``runs/{run_id}/`` directory.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import clock, ids
from .approval import ApprovalGate
from .environment import capture_environment
from .events import EventStore, EventType
from .executor import get_executor
from .leases import LeaseManager
from .logging_json import JsonLogger
from .report import ReportGenerator
from .runner import TaskRunner
from .seeds import SeedManager
from .tools import ToolRegistry
from .validators import ValidatorRegistry

VALID_MODES = {"test", "full", "audit_only", "dry_run"}
VALID_EXECUTORS = {"local", "gpu", "slurm", "dry_run", "audit_only"}


@dataclass(frozen=True)
class RunConfig:
    run_id: str
    mode: str = "test"
    executor: str = "local"
    max_workers: int = 4
    seed: int = 42
    allow_gpu: bool = False
    allow_network: bool = False
    allow_overwrite: bool = False
    approval_policy: str = "strict"
    recovery_policy: str = "requeue_expired_leases"
    logging: str = "json"
    output_dir: str = ""

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(f"invalid mode {self.mode!r}; choices: {sorted(VALID_MODES)}")
        if self.executor not in VALID_EXECUTORS:
            raise ValueError(
                f"invalid executor {self.executor!r}; choices: {sorted(VALID_EXECUTORS)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def config_hash(self) -> str:
        return ids.config_hash(self.to_dict())


class Run:
    """Owns the run directory and all run-scoped services."""

    def __init__(self, config: RunConfig, *, base_dir: str | Path = "runs", clock_fn=clock.iso_now):
        self.config = config
        self._clock = clock_fn
        out = config.output_dir or str(Path(base_dir) / config.run_id)
        self.dir = Path(out)
        for sub in ("events", "logs", "results"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)

        # Freeze config to disk first (spec §24.13).
        (self.dir / "RUN_CONFIG.json").write_text(
            json.dumps({**config.to_dict(), "config_hash": config.config_hash}, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        self.events = EventStore(self.dir / "events" / "run.events.jsonl", clock=clock_fn)
        self.logger = JsonLogger(
            self.dir / "logs" / "run.log.jsonl", context={"run_id": config.run_id}, clock=clock_fn
        )
        self.seeds = SeedManager(config.seed, required=(config.mode in ("full", "test")))
        self.tools = ToolRegistry()
        self.validators = ValidatorRegistry()
        self.approval = ApprovalGate(
            policy=config.approval_policy,
            events=self.events,
            allow_overwrite=config.allow_overwrite,
            persist_path=self.dir / "approvals.json",
        )
        self.executor = get_executor(
            config.executor if config.mode != "dry_run" else "dry_run",
            self.dir / "logs",
            clock_fn=clock_fn,
            disk_path=self.dir,
        )
        self.leases = LeaseManager(events=self.events)
        self.report = ReportGenerator(self.dir)

        self.events.emit(
            EventType.RUN_CREATED,
            run_id=config.run_id,
            config_hash=config.config_hash,
            mode=config.mode,
            executor=config.executor,
        )
        self.logger.info("run created", config_hash=config.config_hash, mode=config.mode)

    def capture_environment(self) -> dict[str, Any]:
        snap = capture_environment(self.dir, timestamp_iso=self._clock(), disk_path=self.dir)
        self.events.emit(
            EventType.ENVIRONMENT_CAPTURED,
            host=snap.get("hardware", {}).get("hostname"),
            git_commit=snap.get("git", {}).get("commit"),
        )
        return snap

    def load_tools(self, tools_dir: str | Path) -> None:
        for contract in self.tools.load_dir(tools_dir):
            self.events.emit(
                EventType.TOOL_DETECTED,
                tool_id=contract.tool_id,
                available=contract.available,
                version=contract.detected_version,
            )

    def build_runner(self, worker_id: str = "worker-0") -> TaskRunner:
        """The only sanctioned way to execute tasks in this run (audit P0.1)."""
        return TaskRunner(
            events=self.events,
            tools=self.tools,
            validators=self.validators,
            approval=self.approval,
            executor=self.executor,
            leases=self.leases,
            results_dir=self.dir / "results",
            seeds=self.seeds,
            worker_id=worker_id,
            clock_fn=clock.monotonic,
        )

    def write_tools_lock(self) -> Path:
        """Freeze detected tool versions (audit P1.9)."""
        lock = {tid: {"version": c.detected_version, "available": c.available, "path": c.executable_path}
                for tid, c in self.tools.all().items()}
        path = self.dir / "TOOLS.lock.json"
        path.write_text(json.dumps(lock, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def finish(self) -> None:
        self.events.emit(EventType.RUN_FINISHED, run_id=self.config.run_id)
        self.logger.info("run finished")
        self.logger.close()


def new_run_id(suffix: str = "001") -> str:
    return ids.run_id(clock.iso_now(), suffix)
