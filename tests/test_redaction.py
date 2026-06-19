"""Audit P1.4: secrets in env VALUES (not just key names) are redacted."""
from harness.redaction import redact, redact_env


def test_url_credentials_redacted():
    out = redact("postgres://user:SuperSecret123@host:5432/db")
    assert "SuperSecret123" not in out
    assert "user:<redacted>@host" in out


def test_env_value_with_embedded_secret_is_masked():
    env = {"DB_CONN": "postgres://user:SuperSecret123@host/db"}
    out = redact_env(env)
    assert "SuperSecret123" not in out["DB_CONN"]


def test_sensitive_key_masks_whole_value():
    out = redact_env({"MY_API_KEY": "anything-here"})
    assert out["MY_API_KEY"] == "<redacted>"


def test_keyvalue_secret_redacted():
    assert "abc123" not in redact("password=abc123")
    assert "TOKEN" not in redact("token = TOKENVALUE") or "<redacted>" in redact("token=TOKENVALUE")


def test_plain_value_untouched():
    assert redact_env({"PATH": "/usr/bin:/bin"})["PATH"] == "/usr/bin:/bin"
