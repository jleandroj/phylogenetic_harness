"""Audit P1.8: sandbox wrapping is correct and opt-in; tool-path whitelist."""
import pytest

from harness.sandbox import build_wrapped, wrap
from harness.tools import ToolContract, ToolRegistry, UntrustedToolPathError


def test_disabled_returns_bare_argv():
    argv = ["samtools", "faidx", "x.fa"]
    out, mode = wrap(argv, enabled=False)
    assert out == argv and mode == "disabled"


def test_no_backend_falls_back_without_pretending(monkeypatch):
    import harness.sandbox as sb
    monkeypatch.setattr(sb.shutil, "which", lambda _b: None)  # no apptainer/bwrap
    out, mode = wrap(["echo", "hi"], enabled=True)
    assert out == ["echo", "hi"] and mode == "no_backend"


def test_apptainer_wrapping_shape():
    out = build_wrapped("apptainer", ["samtools", "faidx", "x.fa"], image="bio.sif")
    assert out[:3] == ["apptainer", "exec", "--containall"]
    assert out[-3:] == ["samtools", "faidx", "x.fa"]
    assert "bio.sif" in out


def test_bwrap_wrapping_shape():
    out = build_wrapped("bwrap", ["echo", "hi"])
    assert out[0] == "bwrap"
    assert "--unshare-net" in out          # network isolated by default
    assert out[-2:] == ["echo", "hi"]


def test_apptainer_requires_image():
    with pytest.raises(ValueError):
        build_wrapped("apptainer", ["x"])


def test_untrusted_tool_path_rejected():
    reg = ToolRegistry(trusted_prefixes=["/opt/trusted"])
    reg.register(ToolContract(tool_id="py", tool_name="python", version_command=["python", "--version"]))
    # python resolves under conda/usr, not /opt/trusted -> rejected.
    with pytest.raises(UntrustedToolPathError):
        reg.require_runnable("py")


def test_trusted_prefix_allows():
    reg = ToolRegistry()  # no whitelist -> normal behaviour
    reg.register(ToolContract(tool_id="py", tool_name="python", version_command=["python", "--version"]))
    assert reg.require_runnable("py").available
