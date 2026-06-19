"""Secret redaction for logs and captured output (audit P1.10).

Best-effort masking of common credential shapes before anything is persisted.
This is a safety net, not a guarantee: it reduces the blast radius of a tool that
prints a token, but the real fix is never to pass secrets to tools that echo them.
"""
from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA<redacted>"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer <redacted>"),
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "sk-<redacted>"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "gh<redacted>"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}"), "<redacted-jwt>"),
    # key=value style secrets
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)\b(\s*[:=]\s*)(\S+)"),
     r"\1\2<redacted>"),
]

# Environment variables whose values must never be written to the snapshot.
SENSITIVE_ENV_KEYS = re.compile(
    r"(?i)(secret|token|password|passwd|api[_-]?key|access[_-]?key|private|credential|auth)"
)


def redact(text: str) -> str:
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact_env(env: dict[str, str]) -> dict[str, str]:
    """Mask values of sensitive-looking environment variables."""
    out: dict[str, str] = {}
    for k, v in env.items():
        out[k] = "<redacted>" if SENSITIVE_ENV_KEYS.search(k) else v
    return out
