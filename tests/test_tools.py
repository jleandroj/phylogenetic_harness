import pytest

from harness.tools import (
    ToolContract,
    ToolRegistry,
    ToolUnavailableError,
    UnregisteredToolError,
)


def test_unregistered_tool_refused():
    reg = ToolRegistry()
    with pytest.raises(UnregisteredToolError):
        reg.get("samtools")


def test_load_dir_detects_versions():
    reg = ToolRegistry()
    import pathlib
    tools_dir = pathlib.Path(__file__).resolve().parent.parent / "tools"
    loaded = reg.load_dir(tools_dir)
    ids = {c.tool_id for c in loaded}
    assert {"samtools", "mafft", "bcftools", "iqtree2"} <= ids


def test_registered_but_unavailable_tool_blocks_run():
    reg = ToolRegistry()
    contract = ToolContract(
        tool_id="ghosttool",
        tool_name="ghosttool",
        version_command=["definitely-not-a-real-binary-xyz", "--version"],
    )
    reg.register(contract)
    assert contract.available is False
    assert contract.detected_version is None
    # Registered, but require_runnable refuses because the executable is absent.
    with pytest.raises(ToolUnavailableError):
        reg.require_runnable("ghosttool")


def test_present_tool_is_runnable():
    reg = ToolRegistry()
    contract = ToolContract(
        tool_id="py", tool_name="python", version_command=["python", "--version"]
    )
    reg.register(contract)
    assert contract.available is True
    assert reg.require_runnable("py").detected_version
