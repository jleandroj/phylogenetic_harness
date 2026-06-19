"""Environment capture (spec §7).

Runs the inventory commands and writes four artefacts so a run never relies on
human memory of "which version I think I used":

    ENVIRONMENT.snapshot.json   structured snapshot (commands + hardware + git)
    ENVIRONMENT.commands.log    raw stdout/stderr of every probe command
    ENVIRONMENT.tools.tsv       tool -> path -> version table
    ENVIRONMENT.hardware.json   hardware profile (from harness.hardware)

Every probe is best-effort: a missing tool is recorded as absent, never fatal.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import hardware

# (label, argv). Best-effort; failures are captured, not raised.
PROBE_COMMANDS: list[tuple[str, list[str]]] = [
    ("uname", ["uname", "-a"]),
    ("whoami", ["whoami"]),
    ("pwd", ["pwd"]),
    ("python_version", ["python", "--version"]),
    ("conda_info", ["conda", "info"]),
    ("git_head", ["git", "rev-parse", "HEAD"]),
    ("git_status", ["git", "status", "--porcelain"]),
]

# Tools whose presence + version we tabulate (spec §5/§7).
TOOL_VERSION_COMMANDS: dict[str, list[str]] = {
    "python": ["python", "--version"],
    "conda": ["conda", "--version"],
    "mamba": ["mamba", "--version"],
    "git": ["git", "--version"],
    "samtools": ["samtools", "--version"],
    "bcftools": ["bcftools", "--version"],
    "mafft": ["mafft", "--version"],
    "iqtree2": ["iqtree2", "--version"],
    "raxml-ng": ["raxml-ng", "--version"],
    "cactus": ["cactus", "--version"],
    "halValidate": ["halValidate", "--version"],
    "nvidia-smi": ["nvidia-smi", "--version"],
}


def _run(argv: list[str], timeout: int = 60) -> dict[str, Any]:
    exe = shutil.which(argv[0])
    if exe is None:
        return {"present": False, "exit_code": None, "stdout": "", "stderr": "not found"}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return {
            "present": True,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "path": exe,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"present": True, "exit_code": None, "stdout": "", "stderr": str(exc), "path": exe}


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def detect_tools() -> dict[str, dict[str, Any]]:
    tools: dict[str, dict[str, Any]] = {}
    for name, argv in TOOL_VERSION_COMMANDS.items():
        path = shutil.which(argv[0])
        if path is None:
            tools[name] = {"present": False, "path": None, "version": None}
            continue
        res = _run(argv)
        version = _first_line(res.get("stdout") or "") or _first_line(res.get("stderr") or "")
        tools[name] = {"present": True, "path": path, "version": version or None}
    return tools


def capture_environment(
    out_dir: str | os.PathLike[str],
    *,
    timestamp_iso: str,
    disk_path: str | os.PathLike[str] = ".",
) -> dict[str, Any]:
    """Capture the environment into ``out_dir`` and return the snapshot dict."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. Probe commands -> commands.log + structured map.
    commands: dict[str, Any] = {}
    log_lines: list[str] = []
    for label, argv in PROBE_COMMANDS:
        res = _run(argv)
        commands[label] = res
        log_lines.append(f"### {label}: {' '.join(argv)}")
        log_lines.append(f"# present={res['present']} exit={res['exit_code']}")
        if res.get("stdout"):
            log_lines.append(res["stdout"].rstrip("\n"))
        if res.get("stderr"):
            log_lines.append("[stderr] " + res["stderr"].rstrip("\n"))
        log_lines.append("")
    (out / "ENVIRONMENT.commands.log").write_text("\n".join(log_lines), encoding="utf-8")

    # 2. Tools table -> tools.tsv.
    tools = detect_tools()
    tsv = ["tool\tpresent\tpath\tversion"]
    for name, t in tools.items():
        tsv.append(f"{name}\t{t['present']}\t{t['path'] or ''}\t{t['version'] or ''}")
    (out / "ENVIRONMENT.tools.tsv").write_text("\n".join(tsv) + "\n", encoding="utf-8")

    # 3. Hardware -> hardware.json.
    hw = hardware.hardware_snapshot(disk_path)
    (out / "ENVIRONMENT.hardware.json").write_text(
        json.dumps(hw, indent=2, sort_keys=True), encoding="utf-8"
    )

    # 4. git provenance (absent is recorded, not fatal — spec runs without git too).
    git_head = commands.get("git_head", {})
    git = {
        "available": git_head.get("present") and git_head.get("exit_code") == 0,
        "commit": _first_line(git_head.get("stdout", "")) or None,
        "dirty": bool((commands.get("git_status", {}).get("stdout") or "").strip()),
    }

    snapshot = {
        "captured_at": timestamp_iso,
        "user": os.environ.get("USER") or commands.get("whoami", {}).get("stdout", "").strip(),
        "cwd": str(Path.cwd()),
        "path_env": os.environ.get("PATH"),
        "ld_library_path": os.environ.get("LD_LIBRARY_PATH"),
        "conda_default_env": os.environ.get("CONDA_DEFAULT_ENV"),
        "commands": commands,
        "tools": tools,
        "hardware": hw,
        "git": git,
    }
    (out / "ENVIRONMENT.snapshot.json").write_text(
        json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8"
    )
    return snapshot
