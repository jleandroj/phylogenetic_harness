import os
import sys
from pathlib import Path

import pytest

# Make the package importable when running pytest from the repo root.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

TOOLS_DIR = REPO / "tools"

# Explicitly expose the dedicated phylo_extra env (iqtree/astral) via the public
# mechanism — import no longer mutates PATH (audit round 4).
_EXTRA = Path.home() / "miniconda3" / "envs" / "phylo_extra" / "bin"
if _EXTRA.is_dir():
    existing = os.environ.get("HARNESS_TOOL_PATHS", "")
    os.environ["HARNESS_TOOL_PATHS"] = f"{_EXTRA}{os.pathsep}{existing}" if existing else str(_EXTRA)
from harness.toolpaths import ensure_tool_paths  # noqa: E402

ensure_tool_paths()

from harness import clock  # noqa: E402
from harness.approval import ApprovalGate  # noqa: E402
from harness.events import EventStore  # noqa: E402
from harness.executor import LocalExecutor  # noqa: E402
from harness.leases import LeaseManager  # noqa: E402
from harness.runner import TaskRunner  # noqa: E402
from harness.seeds import SeedManager  # noqa: E402
from harness.tools import ToolRegistry  # noqa: E402
from harness.validators import ValidatorRegistry  # noqa: E402


def build_runner(base: Path, *, output_cap_bytes: int = 10 * 1024 * 1024, worker_id="worker-0"):
    """Assemble a real TaskRunner over real components (no mocks)."""
    base.mkdir(parents=True, exist_ok=True)
    (base / "logs").mkdir(exist_ok=True)
    (base / "events").mkdir(exist_ok=True)
    events = EventStore(base / "events" / "run.events.jsonl", clock=clock.counting_clock(), worker=worker_id)
    tools = ToolRegistry()
    tools.load_dir(TOOLS_DIR)
    runner = TaskRunner(
        events=events,
        tools=tools,
        validators=ValidatorRegistry(),
        approval=ApprovalGate(events=events),
        executor=LocalExecutor(base / "logs", clock_fn=clock.counting_clock(), disk_path=base,
                               output_cap_bytes=output_cap_bytes),
        leases=LeaseManager(events=events),
        results_dir=base / "results",
        seeds=SeedManager(42),
        worker_id=worker_id,
        clock_fn=clock.monotonic,
    )
    return runner, events, tools


@pytest.fixture
def runner_factory(tmp_path):
    def _make(**kw):
        return build_runner(tmp_path / "run", **kw)
    return _make


@pytest.fixture
def run_dir(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    return d


@pytest.fixture
def tiny_fasta(tmp_path):
    p = tmp_path / "tiny.fa"
    p.write_text(">seqA\nACGTACGT\n>seqB\nACGTAAAA\n", encoding="utf-8")
    return p


@pytest.fixture
def bad_fasta_dupes(tmp_path):
    p = tmp_path / "dupes.fa"
    p.write_text(">seqA\nACGT\n>seqA\nTTTT\n", encoding="utf-8")
    return p


@pytest.fixture
def bad_fasta_residues(tmp_path):
    p = tmp_path / "bad.fa"
    p.write_text(">seqA\nACGTXZ123\n", encoding="utf-8")
    return p


@pytest.fixture
def tiny_newick(tmp_path):
    p = tmp_path / "tree.nwk"
    p.write_text("((homo_sapiens:0.1,pan_troglodytes:0.1):0.2,pongo_abelii:0.3);", encoding="utf-8")
    return p


@pytest.fixture
def tiny_vcf(tmp_path):
    p = tmp_path / "tiny.vcf"
    p.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t100\t.\tA\tT\t50\tPASS\t.\n",
        encoding="utf-8",
    )
    return p
