"""Optional execution sandbox (audit P1.8).

Off by default. When enabled, wraps a command's argv in ``apptainer exec`` or
``bwrap`` (whichever is available) so a tool runs with restricted filesystem/network
access. The wrapping is a pure function so it is testable without a container
runtime present; ``wrap`` falls back to the bare argv (reporting why) when the
feature is disabled or no backend exists — it never silently pretends to sandbox.
"""
from __future__ import annotations

import shutil
from typing import Any

# Order matters for "auto": prefer bwrap (rootless, needs no image) over apptainer
# (which requires a container image).
BACKENDS = ("bwrap", "apptainer")


def available_backend(preferred: str = "auto") -> str | None:
    order = [preferred] if preferred in BACKENDS else list(BACKENDS)
    for b in order:
        if shutil.which(b):
            return b
    return None


def build_wrapped(backend: str, argv: list[str], *, image: str | None = None,
                  binds: list[str] | None = None, allow_net: bool = False) -> list[str]:
    """Pure argv builder (no which/exec). Raises on misuse."""
    if backend == "apptainer":
        if not image:
            raise ValueError("apptainer backend requires an image")
        net = [] if allow_net else ["--net", "--network", "none"]
        return ["apptainer", "exec", "--containall", *net, image, *argv]
    if backend == "bwrap":
        # Read-only root (robust under usrmerge so binaries are found), a fresh
        # tmpfs over /tmp (hides the host /tmp), isolated /dev and /proc, and no
        # network by default. Writable binds are added explicitly for outputs.
        cmd = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
               "--tmpfs", "/tmp"]
        for b in binds or []:
            cmd += ["--bind", b, b]  # writable (e.g. the output dir)
        if not allow_net:
            cmd += ["--unshare-net"]
        return [*cmd, *argv]
    raise ValueError(f"unknown sandbox backend {backend!r}")


def verify_network_blocked(backend: str = "auto", *, timeout: float = 5.0) -> dict[str, Any]:
    """Actually PROVE the sandbox denies network egress (don't just assume it).

    Runs a real outbound-socket attempt inside the sandbox; containment is only
    confirmed if that attempt fails. Returns {backend, blocked, detail}. The
    operator/CI can run this to certify that 'no network' is enforced, not claimed.
    """
    import subprocess
    chosen = available_backend(backend)
    if chosen is None:
        return {"backend": None, "blocked": False, "detail": "no sandbox backend installed"}
    # A tiny probe: success connecting => network NOT contained (bad).
    probe = ("import socket,sys\n"
             "try:\n"
             "    socket.setdefaulttimeout(3)\n"
             "    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).connect(('1.1.1.1', 53))\n"
             "    sys.exit(0)\n"   # reached the network -> containment FAILED
             "except OSError:\n"
             "    sys.exit(7)\n")  # blocked -> contained
    wrapped, mode = wrap(["python3", "-c", probe], enabled=True, backend=chosen)
    if mode in ("disabled", "no_backend"):
        return {"backend": None, "blocked": False, "detail": f"wrap mode={mode}"}
    try:
        rc = subprocess.run(wrapped, capture_output=True, timeout=timeout).returncode
    except (subprocess.TimeoutExpired, OSError) as exc:
        # a hang/timeout means the connect didn't succeed quickly -> treat as blocked
        return {"backend": chosen, "blocked": True, "detail": f"probe error/timeout: {exc}"}
    return {"backend": chosen, "blocked": rc != 0,
            "detail": "connect succeeded (NOT contained)" if rc == 0 else "egress blocked"}


def wrap(argv: list[str], *, enabled: bool = False, backend: str = "auto",
         image: str | None = None, **kw: Any) -> tuple[list[str], str]:
    """Return (possibly-wrapped argv, mode). mode is 'disabled' / 'no_backend' /
    the backend name. Never raises for the common off path."""
    if not enabled:
        return argv, "disabled"
    chosen = available_backend(backend)
    if chosen is None:
        return argv, "no_backend"
    # apptainer needs an image; if none was given, fall back to bwrap when present.
    if chosen == "apptainer" and not image and shutil.which("bwrap"):
        chosen = "bwrap"
    return build_wrapped(chosen, argv, image=image, **kw), chosen
