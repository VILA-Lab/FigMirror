"""EventBus: publish, subscribe, replay, ring-truncation, monotonic seq."""
from __future__ import annotations

import threading

import pytest

from figcopy_runner import events
from figcopy_runner.event_bus import (
    EventBus,
    HISTORY_TRUNCATED_TYPE,
)


def _text(body: str) -> events.TextEvent:
    return events.TextEvent(data={"text": body, "is_partial": False})


def test_publish_assigns_monotonic_seq(tmp_path):
    bus = EventBus()
    s1 = bus.publish(tmp_path, "iter", _text("a"))
    s2 = bus.publish(tmp_path, "iter", _text("b"))
    s3 = bus.publish(tmp_path, "iter", _text("c"))
    assert (s1, s2, s3) == (1, 2, 3)
    assert bus.current_seq(tmp_path, "iter") == 3


def test_seq_isolated_per_session_key(tmp_path):
    bus = EventBus()
    bus.publish(tmp_path, "iter", _text("a"))
    bus.publish(tmp_path, "iter", _text("b"))
    s = bus.publish(tmp_path, "refine:abc12345", _text("c"))
    # Different key → fresh seq counter.
    assert s == 1
    assert bus.current_seq(tmp_path, "iter") == 2


def test_subscribe_receives_subsequent_events(tmp_path):
    bus = EventBus()
    q = bus.subscribe(tmp_path, "iter")
    try:
        bus.publish(tmp_path, "iter", _text("hi"))
        msg = q.get(timeout=1)
        assert msg["type"] == "text"
        assert msg["seq"] == 1
        assert msg["data"] == {"text": "hi", "is_partial": False}
    finally:
        bus.unsubscribe(tmp_path, "iter", q)


def test_subscribe_does_not_replay_past_events(tmp_path):
    bus = EventBus()
    bus.publish(tmp_path, "iter", _text("before"))
    q = bus.subscribe(tmp_path, "iter")
    try:
        # No event from before subscription should be in the queue.
        assert q.qsize() == 0
    finally:
        bus.unsubscribe(tmp_path, "iter", q)


def test_unsubscribe_idempotent(tmp_path):
    bus = EventBus()
    q = bus.subscribe(tmp_path, "iter")
    bus.unsubscribe(tmp_path, "iter", q)
    # Second unsubscribe must not raise.
    bus.unsubscribe(tmp_path, "iter", q)
    assert bus.subscriber_count(tmp_path, "iter") == 0


def test_replay_yields_events_after_since_seq(tmp_path):
    bus = EventBus()
    for i in range(5):
        bus.publish(tmp_path, "iter", _text(f"e{i}"))
    out = list(bus.replay(tmp_path, "iter", since_seq=2))
    seqs = [e["seq"] for e in out]
    # since=2 means yield events with seq > 2 → 3, 4, 5.
    assert seqs == [3, 4, 5]


def test_replay_empty_buffer(tmp_path):
    bus = EventBus()
    out = list(bus.replay(tmp_path, "iter", since_seq=0))
    assert out == []


def test_replay_history_truncated_when_oldest_too_new(tmp_path):
    bus = EventBus(ring_cap=3)
    for i in range(5):
        bus.publish(tmp_path, "iter", _text(f"e{i}"))
    # Buffer holds seq 3, 4, 5. Client wants events since seq=1.
    out = list(bus.replay(tmp_path, "iter", since_seq=1))
    # First yielded should be the truncation control event.
    assert out[0]["type"] == HISTORY_TRUNCATED_TYPE
    assert out[0]["data"]["oldest_available_seq"] == 3
    # Remaining are the events seq>1 that ARE in the buffer (3, 4, 5).
    rest_seqs = [e["seq"] for e in out[1:]]
    assert rest_seqs == [3, 4, 5]


def test_replay_no_truncation_when_client_caught_up(tmp_path):
    bus = EventBus(ring_cap=3)
    for i in range(5):
        bus.publish(tmp_path, "iter", _text(f"e{i}"))
    # since=4 → only seq=5 is newer; oldest is 3 so no truncation.
    out = list(bus.replay(tmp_path, "iter", since_seq=4))
    assert [e["type"] for e in out] == ["text"]
    assert [e["seq"] for e in out] == [5]


def test_monotonic_seq_under_concurrent_publishers(tmp_path):
    """Many threads publishing concurrently still produce a contiguous
    1..N seq sequence with no gaps and no duplicates."""
    bus = EventBus()
    num_workers = 8
    per_worker = 64
    total = num_workers * per_worker  # 512
    threads = []
    barrier = threading.Barrier(num_workers)

    def worker():
        barrier.wait()
        for _ in range(per_worker):
            bus.publish(tmp_path, "iter", _text("x"))

    for _ in range(num_workers):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    final = bus.current_seq(tmp_path, "iter")
    assert final == total

    all_seqs = sorted(e["seq"] for e in bus.replay(tmp_path, "iter", since_seq=0))
    # Within the buffered slice, seqs must be contiguous + unique.
    for a, b in zip(all_seqs, all_seqs[1:]):
        assert b == a + 1
    assert len(set(all_seqs)) == len(all_seqs)


def test_publish_mutates_event_seq_in_place(tmp_path):
    bus = EventBus()
    e = _text("hello")
    assert e.seq == 0
    bus.publish(tmp_path, "iter", e)
    assert e.seq == 1
