"""Round 2 / Iter 7: PROVE the sandbox denies network egress (don't assume it).

Containment guarantee #3 includes "no network". This test actually attempts an
outbound socket inside the sandbox and confirms it fails.
"""

import shutil

import pytest

from harness import sandbox


def test_wrap_unshares_net_by_default():
    wrapped = sandbox.build_wrapped("bwrap", ["echo", "hi"])
    assert "--unshare-net" in wrapped


def test_allow_net_opt_in_removes_isolation():
    wrapped = sandbox.build_wrapped("bwrap", ["echo", "hi"], allow_net=True)
    assert "--unshare-net" not in wrapped


@pytest.mark.skipif(
    not shutil.which("bwrap") and not shutil.which("apptainer"),
    reason="no sandbox backend installed",
)
def test_network_egress_is_actually_blocked():
    res = sandbox.verify_network_blocked()
    assert res["backend"] is not None
    assert res["blocked"] is True, f"network NOT contained: {res}"
