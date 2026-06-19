import pytest

from harness import ids
from harness.clock import counting_clock
from harness.events import EventStore, EventType


def test_event_store_appends_jsonl(tmp_path):
    store = EventStore(tmp_path / "e.jsonl", clock=counting_clock())
    store.emit(EventType.RUN_CREATED, run_id="r1")
    store.emit(EventType.TASK_CREATED, task_id="t1")
    events = store.read()
    assert [e["event"] for e in events] == ["run_created", "task_created"]
    assert [e["seq"] for e in events] == [1, 2]
    assert events[0]["ts"] == "t000000"


def test_unknown_event_type_rejected(tmp_path):
    store = EventStore(tmp_path / "e.jsonl")
    with pytest.raises(ValueError):
        store.emit("not_a_real_event")


def test_iter_type_filters(tmp_path):
    store = EventStore(tmp_path / "e.jsonl")
    store.emit(EventType.TASK_FAILED, task_id="t1")
    store.emit(EventType.TASK_SUCCEEDED, task_id="t2")
    store.emit(EventType.TASK_FAILED, task_id="t3")
    failed = list(store.iter_type(EventType.TASK_FAILED))
    assert [e["task_id"] for e in failed] == ["t1", "t3"]


def test_hashes_are_stable_and_order_independent():
    h1 = ids.config_hash({"a": 1, "b": 2})
    h2 = ids.config_hash({"b": 2, "a": 1})
    assert h1 == h2
    # command_hash is stable per representation (string vs argv hash differently
    # on purpose — they are different renderings of the command).
    assert ids.command_hash("samtools faidx x.fa") == ids.command_hash("samtools faidx x.fa")
    assert ids.command_hash(["samtools", "faidx"]) == ids.command_hash(["samtools", "faidx"])


def test_run_id_from_timestamp():
    assert ids.run_id("2026-06-19T13:15:00-04:00", "007") == "run_20260619T131500_007"


def test_sha256_file(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes(b"hello")
    import hashlib
    assert ids.sha256_file(p) == hashlib.sha256(b"hello").hexdigest()
