"""EventBus — in-memory pub-sub for SessionEvents with replay buffer.

Per design.md §D5 + backend spec "In-memory event bus with per-session
ring buffer". Thread-safe via a single coarse lock; performance is
fine at our 5–6-session scale (each publish/subscribe is O(N) over
subscriber queues, but N is small).

Wire model:

- A **session key** is ``(workdir_path_str, slot)`` where
  ``slot ∈ {"iter", "refine:<set_id>"}``. The bus is workdir-keyed,
  but workdir paths are normalized to ``str(Path.resolve())`` so
  ``Path("/a")`` and ``Path("/a/")`` collapse to the same key.
- ``publish(key, event)`` assigns a monotonic seq (per key), appends
  to the per-key ring buffer (cap 1000), and fans out to every
  subscriber queue for that key.
- ``subscribe(key) -> Queue`` returns a new ``queue.Queue`` that
  receives every subsequent event. The caller MUST eventually call
  ``unsubscribe`` (typically in a SSE handler ``finally``).
- ``replay(key, since_seq) -> Iterable[event_dict]`` walks the ring
  buffer; if ``since_seq`` is older than the oldest buffered seq,
  the iterator yields a ``history_truncated`` control dict first so
  the client knows to re-fetch full history.

Stdlib only: ``threading``, ``queue``, ``collections.deque``.
"""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from queue import Queue
from typing import Iterable

from . import events as ev


# Ring buffer cap per session. 1000 is comfortably above any plausible
# turn's event count even with verbose token streaming.
DEFAULT_RING_CAP = 1000

# Control event surfaced when a reconnecting subscriber's
# Last-Event-ID is older than the oldest buffered event.
HISTORY_TRUNCATED_TYPE = "history_truncated"


SessionKey = tuple[str, str]  # (workdir_str, slot)


def _normalize_key(workdir: Path | str, slot: str) -> SessionKey:
    """Coerce to canonical ``(str(resolved_path), slot)``."""
    if isinstance(workdir, Path):
        return (str(workdir.resolve()), slot)
    return (str(Path(workdir).resolve()), slot)


class EventBus:
    """One bus instance per server process. Multi-session via SessionKey."""

    def __init__(self, ring_cap: int = DEFAULT_RING_CAP):
        self._ring_cap = ring_cap
        self._lock = threading.Lock()
        # Per-key state: monotonic seq counter, ring buffer of dict events,
        # and a list of subscriber Queues.
        self._seq: dict[SessionKey, int] = {}
        self._buffer: dict[SessionKey, deque[dict]] = {}
        self._subs: dict[SessionKey, list[Queue]] = {}

    # ───── publish ────────────────────────────────────────────────────

    def publish(self, workdir: Path | str, slot: str,
                event: ev.SessionEvent) -> int:
        """Assign seq, append to buffer, fan out to subscribers.

        Returns the assigned seq. Mutates ``event.seq`` in place so the
        caller can read it back if needed (events are non-frozen
        dataclasses; see events.py).
        """
        key = _normalize_key(workdir, slot)
        payload = ev.event_to_dict(event)
        with self._lock:
            n = self._seq.get(key, 0) + 1
            self._seq[key] = n
            payload["seq"] = n
            event.seq = n
            buf = self._buffer.get(key)
            if buf is None:
                buf = deque(maxlen=self._ring_cap)
                self._buffer[key] = buf
            buf.append(payload)
            # Snapshot subscriber list while still holding the lock so
            # concurrent unsubscribes don't race with the fan-out.
            subs = list(self._subs.get(key, ()))
        # Fan out OUTSIDE the lock — Queue.put can theoretically block
        # if the consumer disappeared, but Queue has no upper bound by
        # default so this is fine in practice. Keeping it out of the
        # lock avoids holding the lock across consumer-thread waits.
        for q in subs:
            try:
                q.put_nowait(payload)
            except Exception:
                # A wedged consumer queue can't be allowed to take down
                # the publisher. Best-effort fan-out; the consumer will
                # be GC'd when its SSE handler returns.
                pass
        return n

    # ───── subscribe / unsubscribe ────────────────────────────────────

    def subscribe(self, workdir: Path | str, slot: str) -> Queue:
        """Return a fresh queue that will receive every subsequent event.

        The caller MUST eventually pass this queue to ``unsubscribe``.
        Suggested pattern in SSE handlers::

            q = bus.subscribe(workdir, slot)
            try:
                while True:
                    event = q.get(timeout=15)
                    ...
            finally:
                bus.unsubscribe(workdir, slot, q)
        """
        key = _normalize_key(workdir, slot)
        q: Queue = Queue()
        with self._lock:
            self._subs.setdefault(key, []).append(q)
        return q

    def unsubscribe(self, workdir: Path | str, slot: str, q: Queue) -> None:
        """Remove a subscriber. Idempotent: missing queue is a no-op."""
        key = _normalize_key(workdir, slot)
        with self._lock:
            subs = self._subs.get(key)
            if not subs:
                return
            try:
                subs.remove(q)
            except ValueError:
                pass
            if not subs:
                # Hygiene: don't leak empty subscriber lists.
                self._subs.pop(key, None)

    # ───── replay ─────────────────────────────────────────────────────

    def replay(self, workdir: Path | str, slot: str,
               since_seq: int) -> Iterable[dict]:
        """Yield buffered events with seq > since_seq.

        If ``since_seq`` is older than the oldest seq still in the
        buffer (i.e. the client missed events that have aged out),
        the iterator yields a synthetic ``history_truncated`` control
        dict first so the client can react (typically by re-fetching
        ``GET /api/runs/<name>/chat/<set_id>``).

        Snapshots the buffer under the lock and yields outside the
        lock, so a slow consumer can't block publishers.
        """
        key = _normalize_key(workdir, slot)
        with self._lock:
            buf = self._buffer.get(key)
            snapshot = list(buf) if buf else []
        if not snapshot:
            return
        oldest_seq = snapshot[0]["seq"]
        if since_seq < oldest_seq - 1:
            # Client wants events older than what we still have.
            yield {
                "type": HISTORY_TRUNCATED_TYPE,
                "seq": oldest_seq,
                "data": {"oldest_available_seq": oldest_seq,
                         "client_last_seq": since_seq},
            }
        for payload in snapshot:
            if payload["seq"] > since_seq:
                yield payload

    # ───── introspection (mostly for tests) ───────────────────────────

    def current_seq(self, workdir: Path | str, slot: str) -> int:
        """Last assigned seq for this key (0 if no events yet)."""
        key = _normalize_key(workdir, slot)
        with self._lock:
            return self._seq.get(key, 0)

    def subscriber_count(self, workdir: Path | str, slot: str) -> int:
        key = _normalize_key(workdir, slot)
        with self._lock:
            return len(self._subs.get(key, ()))


# ───── module-level singleton ─────────────────────────────────────────
#
# One bus per server process. Runners + the SSE handler all import
# this. Tests can construct their own EventBus() instance to avoid
# global state.

_BUS: EventBus | None = None
_BUS_LOCK = threading.Lock()


def get_bus() -> EventBus:
    """Return the process-wide EventBus, lazily constructed."""
    global _BUS
    with _BUS_LOCK:
        if _BUS is None:
            _BUS = EventBus()
        return _BUS
