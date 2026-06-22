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
    # URLs carrying credentials: scheme://user:pass@host (audit P1.4).
    (re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)([^/\s:@]+):([^/\s@]+)@"), r"\1\2:<redacted>@"),
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


# A command-line flag whose value is a secret: --password X, --token=X, --api-key X.
_SECRET_FLAG = re.compile(
    r"(?i)^(--?)(password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|auth|credential)"
    r"(=(.*))?$"
)


def redact_argv(argv: list[str]) -> list[str]:
    """Mask secrets in a command line before it is logged (audit chain/events).

    Handles both ``--token=VALUE`` (inline) and ``--token VALUE`` (separated), plus
    any token that itself looks like a credential (via ``redact``). The REAL argv
    is still what executes — only the logged copy is masked, so a secret passed on
    the command line never lands in the tamper-proof audit log.
    """
    out: list[str] = []
    mask_next = False
    for tok in argv:
        if mask_next:
            out.append("<redacted>")
            mask_next = False
            continue
        m = _SECRET_FLAG.match(tok)
        if m:
            if m.group(3):                       # --token=VALUE
                out.append(f"{m.group(1)}{m.group(2)}=<redacted>")
            else:                                # --token VALUE (mask the next token)
                out.append(tok)
                mask_next = True
            continue
        out.append(redact(tok))
    return out


def redact_env(env: dict[str, str]) -> dict[str, str]:
    """Mask environment variables (audit P1.4).

    A sensitive-looking KEY masks the whole value; otherwise the VALUE is still
    run through ``redact`` so a secret embedded in an innocuously-named variable
    (e.g. ``DB_CONN=postgres://user:pass@host``) is not leaked.
    """
    out: dict[str, str] = {}
    for k, v in env.items():
        out[k] = "<redacted>" if SENSITIVE_ENV_KEYS.search(k) else redact(v)
    return out
