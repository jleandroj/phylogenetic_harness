"""Hardware profiler (spec §6).

Detects CPU, memory, disk and GPU. Degrades gracefully when a probe is absent
(no nvidia-smi, no /proc) instead of crashing, and records *that* it could not
measure rather than guessing.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _read_meminfo_kb(key: str) -> int | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(key + ":"):
                    return int(line.split()[1])  # value in kB
    except OSError:
        return None
    return None


def cpu_profile() -> dict[str, Any]:
    logical = os.cpu_count()
    physical = None
    try:
        # Count distinct (physical id, core id) pairs from /proc/cpuinfo.
        cores: set[tuple[str, str]] = set()
        phys = core = None
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("physical id"):
                    phys = line.split(":")[1].strip()
                elif line.startswith("core id"):
                    core = line.split(":")[1].strip()
                elif line.strip() == "" and phys is not None and core is not None:
                    cores.add((phys, core))
                    phys = core = None
        physical = len(cores) or None
    except OSError:
        physical = None
    return {"logical_cores": logical, "physical_cores": physical}


def memory_profile() -> dict[str, Any]:
    total = _read_meminfo_kb("MemTotal")
    avail = _read_meminfo_kb("MemAvailable")
    swap = _read_meminfo_kb("SwapTotal")
    return {
        "total_gb": round(total / 2 ** 20, 2) if total else None,
        "available_gb": round(avail / 2 ** 20, 2) if avail else None,
        "swap_gb": round(swap / 2 ** 20, 2) if swap else None,
    }


def disk_profile(path: str | os.PathLike[str] = ".") -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
        return {
            "path": str(Path(path).resolve()),
            "total_gb": round(usage.total / 2 ** 30, 2),
            "free_gb": round(usage.free / 2 ** 30, 2),
            "used_gb": round(usage.used / 2 ** 30, 2),
        }
    except OSError:
        return {"path": str(path), "total_gb": None, "free_gb": None, "used_gb": None}


def gpu_profile() -> dict[str, Any]:
    """Probe GPUs via nvidia-smi. Returns availability + per-GPU memory.

    Distinguishes 'no driver/tool' from 'tool present, zero GPUs'. Never assumes
    GPU 0; reports every device the tool lists (spec §6.1).
    """
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return {"available": False, "reason": "nvidia-smi not found", "devices": []}
    try:
        out = subprocess.run(
            [
                smi,
                "--query-gpu=index,name,memory.total,memory.used,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "reason": f"nvidia-smi failed: {exc}", "devices": []}
    if out.returncode != 0:
        return {
            "available": False,
            "reason": f"nvidia-smi exit {out.returncode}: {out.stderr.strip()}",
            "devices": [],
        }
    devices: list[dict[str, Any]] = []
    for line in out.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        idx, name, mem_total, mem_used, cc = parts[:5]
        devices.append(
            {
                "index": int(idx) if idx.isdigit() else idx,
                "name": name,
                "memory_total_mb": float(mem_total) if mem_total else None,
                "memory_used_mb": float(mem_used) if mem_used else None,
                "compute_capability": cc,
            }
        )
    return {
        "available": len(devices) > 0,
        "reason": "ok" if devices else "nvidia-smi present but no devices",
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "devices": devices,
    }


def hardware_snapshot(disk_path: str | os.PathLike[str] = ".") -> dict[str, Any]:
    return {
        "hostname": os.uname().nodename if hasattr(os, "uname") else None,
        "cpu": cpu_profile(),
        "memory": memory_profile(),
        "disk": disk_profile(disk_path),
        "gpu": gpu_profile(),
    }
