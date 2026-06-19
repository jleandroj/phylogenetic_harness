"""Audit P0.4/P0.5: N real processes write the same event log with no corruption.

Uses the spawn context (spec §24.10). The worker is module-level so it is
importable under spawn.
"""
import multiprocessing as mp

from harness.events import EventStore, EventType

N_PROCS = 8
K_EVENTS = 200


def _worker(path: str, worker_id: str, k: int) -> None:
    store = EventStore(path, worker=worker_id)
    for i in range(k):
        store.emit(EventType.WORKER_HEARTBEAT, worker_id=worker_id, i=i)


def test_concurrent_writers_no_corruption(tmp_path):
    path = str(tmp_path / "events.jsonl")
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_worker, args=(path, f"w{n}", K_EVENTS)) for n in range(N_PROCS)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
        assert p.exitcode == 0

    store = EventStore(path)
    events = store.read()  # read() json-parses every line; a torn line would raise
    assert len(events) == N_PROCS * K_EVENTS

    # Global sequence numbers are unique and contiguous (no collisions).
    seqs = sorted(e["seq"] for e in events)
    assert seqs == list(range(1, N_PROCS * K_EVENTS + 1))

    # Every worker landed exactly K events.
    from collections import Counter
    per_worker = Counter(e["worker"] for e in events)
    assert all(v == K_EVENTS for v in per_worker.values())
    assert len(per_worker) == N_PROCS
