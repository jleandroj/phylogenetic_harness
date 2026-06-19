"""Tool registry and contracts (spec §5, §24.2).

A bioinformatics command may only run if its tool is registered as a formal
contract. The registry detects the tool's real path and version at load time so
provenance records the *actual* version, not an assumed one. A tool whose
executable is absent is registered with ``detected_version=None`` and
``available=False`` (registered-but-unavailable), e.g. iqtree2 on this host.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class UnregisteredToolError(Exception):
    """Raised when execution is attempted with a tool not in the registry."""


class ToolUnavailableError(Exception):
    """Raised when a registered tool's executable is not present on this host."""


@dataclass
class ToolContract:
    tool_id: str
    tool_name: str
    version_command: list[str] = field(default_factory=list)
    requires_gpu: bool = False
    requires_network: bool = False
    input_formats: list[str] = field(default_factory=list)
    output_formats: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    validators: list[str] = field(default_factory=list)
    container: str | None = None
    environment: str | None = None
    # CLI flag this tool uses to accept a seed (audit P1.9), e.g. "--seed".
    seed_flag: str | None = None
    # Filled by detect().
    executable_path: str | None = None
    detected_version: str | None = None
    available: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ToolContract:
        vc = d.get("version_command")
        if isinstance(vc, str):
            vc = vc.split()
        return cls(
            tool_id=d["tool_id"],
            tool_name=d.get("tool_name", d["tool_id"]),
            version_command=vc or [],
            requires_gpu=bool(d.get("requires_gpu", False)),
            requires_network=bool(d.get("requires_network", False)),
            input_formats=list(d.get("input_formats", [])),
            output_formats=list(d.get("output_formats", [])),
            risks=list(d.get("risks", [])),
            validators=list(d.get("validators", [])),
            container=d.get("container"),
            environment=d.get("environment"),
            seed_flag=d.get("seed_flag"),
        )

    def detect(self) -> ToolContract:
        """Resolve executable path and version on this host (best-effort)."""
        if not self.version_command:
            return self
        exe = shutil.which(self.version_command[0])
        self.executable_path = exe
        self.available = exe is not None
        if not self.available:
            self.detected_version = None
            return self
        try:
            proc = subprocess.run(
                self.version_command, capture_output=True, text=True, timeout=60
            )
            text = proc.stdout or proc.stderr or ""
            for line in text.splitlines():
                if line.strip():
                    self.detected_version = line.strip()
                    break
        except (OSError, subprocess.SubprocessError):
            self.detected_version = None
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "version_command": self.version_command,
            "requires_gpu": self.requires_gpu,
            "requires_network": self.requires_network,
            "input_formats": self.input_formats,
            "output_formats": self.output_formats,
            "risks": self.risks,
            "validators": self.validators,
            "container": self.container,
            "environment": self.environment,
            "seed_flag": self.seed_flag,
            "executable_path": self.executable_path,
            "detected_version": self.detected_version,
            "available": self.available,
        }


class UntrustedToolPathError(Exception):
    """Raised when a tool's executable resolves outside the trusted path prefixes."""


class ToolRegistry:
    def __init__(self, *, trusted_prefixes: list[str] | None = None) -> None:
        self._tools: dict[str, ToolContract] = {}
        # Audit P1.8: if set, a tool is only runnable when its executable lives
        # under one of these path prefixes (defence against PATH hijacking).
        self.trusted_prefixes = [str(p) for p in trusted_prefixes] if trusted_prefixes else None

    def register(self, contract: ToolContract, *, detect: bool = True) -> ToolContract:
        if detect:
            contract.detect()
        self._tools[contract.tool_id] = contract
        return contract

    def load_dir(self, directory: str | Path, *, detect: bool = True) -> list[ToolContract]:
        loaded: list[ToolContract] = []
        for path in sorted(Path(directory).glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            loaded.append(self.register(ToolContract.from_dict(data), detect=detect))
        return loaded

    def get(self, tool_id: str) -> ToolContract:
        if tool_id not in self._tools:
            raise UnregisteredToolError(
                f"tool '{tool_id}' is not registered; refusing to run an unregistered tool"
            )
        return self._tools[tool_id]

    def require_runnable(self, tool_id: str) -> ToolContract:
        """Return the contract only if registered, present, AND (when configured)
        its executable lives under a trusted path prefix."""
        contract = self.get(tool_id)
        if not contract.available:
            raise ToolUnavailableError(
                f"tool '{tool_id}' is registered but unavailable on this host "
                f"(executable not found)"
            )
        if self.trusted_prefixes is not None:
            path = contract.executable_path or ""
            if not any(path.startswith(prefix) for prefix in self.trusted_prefixes):
                raise UntrustedToolPathError(
                    f"tool '{tool_id}' executable {path!r} is not under a trusted prefix "
                    f"{self.trusted_prefixes}"
                )
        return contract

    def all(self) -> dict[str, ToolContract]:
        return dict(self._tools)
