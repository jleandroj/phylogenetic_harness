"""Audit P1.5: the event store assigns seq in O(1), not O(n) per emit."""
import time

from harness.events import EventStore, EventType

N = 20_000


def test_throughput_and_unique_seq(tmp_path):
    store = EventStore(tmp_path / "e.jsonl", fsync_every=0)  # flush-only for speed
    t0 = time.monotonic()
    for i in range(N):
        store.emit(EventType.WORKER_HEARTBEAT, i=i)
    elapsed = time.monotonic() - t0

    # O(n^2) (re-counting the file each emit) would blow far past this.
    assert elapsed < 10.0, f"{N} emits took {elapsed:.1f}s"

    events = store.read()
    assert len(events) == N
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, N + 1))  # contiguous, unique
