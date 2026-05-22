"""chat_log — shared atomic-append helper for ``{workdir}/chat.jsonl``.

Per design.md §D4 + backend spec, every Phase-2 user message and
agent reply is appended to ``chat.jsonl`` in the workdir, indexed by
``set_id`` so a single run can hold many parallel chats (one per
selected baseline set). The file is the **source of truth** for chat
history across page refresh + cross-device access; localStorage on
the client is a write-through cache.

Atomicity strategy
------------------

JSONL files are typically appended in-place, but in-place append is
NOT atomic under POSIX (a crash mid-write can leave a torn line and
break the entire file's JSONL parse). The chat.jsonl files we expect
are small (≤ a few hundred entries per chat × ≤ a few chats per run),
so we use the **read-all + rewrite-whole-file** pattern with a
``.tmp`` + rename swap. Compared to in-place append:

- O(N) per append instead of O(1) — but N is small.
- Atomic — no torn lines, no half-flushed final entry.
- Same idiom as ``status.json`` / ``sessions.json``.

If files ever grow large enough for the O(N) cost to matter, switch
to ``fcntl.flock`` + in-place append. We're nowhere near that point.

Schema
------

Each line is one JSON object::

    {
      "ts": "2026-05-12T14:23:01Z",
      "role": "user" | "assistant",
      "content": "...",                # text body
      "set_id": "abc12345",
      "baseline_iters": [1, 3, 5],
      # assistant-only extras:
      "image_url": "refine_007.png",
      "rcparams_delta": {...},
      "review": "...",
      "refine_idx": 7,
      "seq": 42,                       # event-bus seq of refine_complete
      # user-only extras:
      "adjustments": {"font.size": 15},  # when present
    }
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .interface import (
    TERMINAL_RUN_STATUSES,
    active_refine_in_flight_set_ids,
    atomic_write_text,
    clear_refine_in_flight_for_idx,
    compute_set_id,
    read_status_sidecar,
)


CHAT_FILE = "chat.jsonl"


def _now_iso() -> str:
    """UTC ISO-8601 timestamp with trailing Z (no microseconds)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _active_refine_set_ids(workdir: Path) -> set[str]:
    """Return set_ids the runner has marked as having an in-flight refine.

    Reads the per-``set_id`` ``.refine_inflight_<sid>`` markers each
    runner writes at the TOP of ``_refine_locked`` (before the CLI
    subprocess is spawned) and removes in a ``finally`` after
    ``append_turn`` succeeds. Used by ``recover_orphan_refines`` to
    carve a hole in the terminal-status gate for the canonical Step-2
    user flow: a refine on an already-shipped run, where the runner
    started a CLI subprocess that wrote the artifact pair to disk but
    where the runner died (or the HTTP request was abandoned) before
    appending the assistant chat bubble. Without this carve-out, the
    recovered turn would be silently swallowed by the terminal-status
    check (PR #25 round-1 finding #1).

    PR #25 round-2 follow-up: an earlier fix keyed this on
    ``sessions["refine"][set_id]``, but that key is written AFTER the
    subprocess completes (claude.py:462 / codex.py:754) and only when
    the agent emitted a ``session_id``. Two regressions followed:

    1. **Early-crash variant**: subprocess writes
       ``refine_NNN.{png,json}`` then SIGKILL's before the runner
       reaches ``write_sessions`` → the carve-out silently swallows
       the artifacts on a terminal-state run.
    2. **Stale-key variant**: ``sessions["refine"]`` has no
       ``pop``/``del`` callsite — entries from prior completed turns
       persist forever, permanently bypassing the round-2 invariant
       for any same-set artifact pair that lands later via out-of-band
       tooling.

    Both close once the carve-out keys on a write-at-entry,
    clear-at-exit marker that matches the docstring's claimed
    ordering. Defensive: returns an empty set if the workdir glob
    fails (matches the prior fallback behavior when ``sessions.json``
    was malformed).
    """
    return active_refine_in_flight_set_ids(workdir)


def append_turn(workdir: Path, *, role: str, content: str,
                set_id: str, baseline_iters: list[int],
                **extras) -> dict:
    """Append one turn to ``{workdir}/chat.jsonl`` atomically.

    Reads the existing file (if any), appends the new entry, rewrites
    via ``.tmp`` + rename. Returns the appended entry dict so the
    caller can echo it back / log it.

    ``role`` should be ``"user"`` or ``"assistant"`` (not validated
    here — schema enforcement lives at the route layer). ``**extras``
    folds in image_url / rcparams_delta / review / refine_idx / seq /
    adjustments per the schema above.

    Idempotency: when ``role == "assistant"`` and ``extras`` carries
    a ``refine_idx`` (i.e., a refine-completion entry), the call is
    a no-op if a prior assistant turn for the same
    ``(set_id, refine_idx)`` is already on disk. Returns that prior
    entry instead of double-appending. This protects against the
    runner-vs-recovery race in ``recover_orphan_refines``: either
    side can win, but they cannot both append duplicate bubbles for
    the same artifact pair. User turns and assistant turns without
    a ``refine_idx`` (e.g., free-text replies) are never deduped —
    they may legitimately repeat.
    """
    workdir = workdir.resolve()
    refine_idx = extras.get("refine_idx") if role == "assistant" else None
    if isinstance(refine_idx, int):
        for prior in read_turns(workdir, set_id=set_id):
            if (
                prior.get("role") == "assistant"
                and prior.get("set_id") == set_id
                and prior.get("refine_idx") == refine_idx
            ):
                return prior

    entry: dict = {
        "ts": _now_iso(),
        "role": role,
        "content": content,
        "set_id": set_id,
        "baseline_iters": list(baseline_iters),
    }
    entry.update(extras)

    path = workdir / CHAT_FILE
    existing_lines: list[str] = []
    if path.exists():
        try:
            existing_lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            # If the existing file is unreadable, we *could* refuse and
            # raise — but the user shouldn't lose the new turn over an
            # earlier disk hiccup. Treat as empty + log to stderr.
            print(
                f"[chat_log] {path} unreadable; rewriting from scratch",
                file=sys.stderr, flush=True,
            )

    new_line = json.dumps(entry, ensure_ascii=False)
    all_lines = [ln for ln in existing_lines if ln.strip()] + [new_line]
    atomic_write_text(path, "\n".join(all_lines) + "\n")
    return entry


def read_turns(workdir: Path, *, set_id: str | None = None) -> list[dict]:
    """Read chat.jsonl, optionally filtering by ``set_id``.

    Missing file → ``[]``. Malformed lines are silently skipped (one
    bad line shouldn't kill the rest of the chat). Returns entries in
    file order (= chronological order, since we always append).
    """
    workdir = workdir.resolve()
    path = workdir / CHAT_FILE
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue
        if set_id is not None and entry.get("set_id") != set_id:
            continue
        out.append(entry)
    return out


def recover_orphan_refines(workdir: Path, *, set_id: str | None = None) -> list[dict]:
    """Append assistant turns for on-disk refine outputs missing from chat.

    This is a crash/timeout recovery path. If a CLI process writes
    ``refine_NNN.png`` + ``refine_NNN.json`` but the HTTP request dies
    before the runner appends the assistant turn, the next page load or
    chat-history request can still surface the image to the user.

    No-op on terminal runs **unless** the orphan refine belongs to a
    set_id that the runner currently has an in-flight marker for (a
    sidecar file written at the TOP of ``_refine_locked`` and removed
    in a ``finally`` after ``append_turn`` succeeds — see
    ``interface.mark_refine_in_flight`` /
    ``interface.clear_refine_in_flight``). Per the round-2 invariant
    (``TERMINAL_RUN_STATUSES``), a run that has shipped, failed, or
    been cancelled is treated as read-only browsable: a stale orphan
    refine artifact pair lying around on disk MUST NOT trigger a
    mutation of ``chat.jsonl`` on every page-load. The in-flight-marker
    carve-out covers the canonical Step-2 user flow on a shipped run —
    the runner stamps the marker BEFORE the long-lived refine
    subprocess runs, so a crash anywhere from spawn through
    ``append_turn`` (including the early-crash variant where the
    subprocess writes artifacts then SIGKILL's before the runner
    persists the agent session id) leaves a marker on disk and lets
    recovery surface the assistant turn even though run-level state
    is ``"shipped"``. set_ids without a current in-flight marker are
    still gated, preserving the original invariant for unrelated stale
    artifact pairs from prior completed turns.

    PR #25 round-3 finding #2 (recovery clears consumed markers): on
    a terminal-state run, after we have surfaced an orphan pair via
    the carve-out, this function clears the in-flight marker for that
    set_id. Without this, a one-time terminal-state carve-out becomes
    a permanent bypass — any subsequent out-of-band artifact pair under
    the same set_id (out-of-band tooling, partial-write recovery, a
    different runner re-stamping after consumption) would be silently
    surfaced via the still-live marker. The clear is bounded to the
    terminal path because an active run's runner owns the marker
    lifecycle via its own ``finally``; racing it on a still-running
    run would drop the carve-out before the runner finished its turn.
    Stale-marker hardening (host OOM / SIGKILL / PID reuse between
    mark and finally) lives in ``active_refine_in_flight_set_ids`` via
    the pid + ``proc_starttime`` + TTL triple.

    PR #25 round-4 finding #1 (recovery must NOT clear a live runner's
    marker on a terminal-state refine): the carve-out's whole purpose
    is to allow Step-2 refines to fire WHILE ``status.json["state"]``
    is ``shipped``/``failed``/``cancelled``, so ``in_terminal=True``
    is precisely the case where a runner CAN be live. We use
    ``clear_refine_in_flight_for_idx`` (not the unconditional
    ``clear_refine_in_flight``) and pass the consumed orphan's
    ``refine_idx`` — clear only fires when the marker's stamped index
    matches the consumed orphan's index. So an older same-set orphan
    (a leftover from a prior crashed turn at index N-1) consumed
    while the active runner holds a marker at index N will leave the
    active runner's marker untouched.
    """
    workdir = workdir.resolve()

    # Terminal-status gate (round-2 invariant; round-4 fix; PR #25
    # round-1 carve-out for active-refine set_ids on shipped runs).
    sidecar = read_status_sidecar(workdir) or {}
    state = sidecar.get("state")
    in_terminal = isinstance(state, str) and state in TERMINAL_RUN_STATUSES
    allowed_terminal_sids: set[str] | None = None
    if in_terminal:
        allowed_terminal_sids = _active_refine_set_ids(workdir)
        if set_id is not None:
            if set_id not in allowed_terminal_sids:
                return []
        elif not allowed_terminal_sids:
            return []

    existing = read_turns(workdir, set_id=set_id)
    seen: set[tuple[str, int]] = set()
    for entry in existing:
        if entry.get("role") != "assistant":
            continue
        sid = entry.get("set_id")
        idx = entry.get("refine_idx")
        if isinstance(sid, str) and isinstance(idx, int):
            seen.add((sid, idx))

    recovered: list[dict] = []
    # PR #25 round-4 finding #1: track (set_id, refine_idx) of every
    # orphan we consume on the terminal path so the post-loop clear
    # can refuse to wipe a live runner's marker stamped under a
    # different refine_idx.
    consumed_terminal_pairs: set[tuple[str, int]] = set()
    for json_path in sorted(workdir.glob("refine_*.json")):
        stem = json_path.stem
        try:
            refine_idx = int(stem.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        png_path = workdir / f"refine_{refine_idx:03d}.png"
        if not png_path.is_file():
            continue
        try:
            outcome = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(outcome, dict):
            continue
        raw_iters = outcome.get("baseline_iters")
        if not isinstance(raw_iters, list) or not raw_iters:
            # Legacy refine outputs written before this commit may not
            # embed ``baseline_iters``; we cannot derive a ``set_id``
            # without it, so we cannot route the recovered turn to a
            # specific chat. Surface this to stderr so operators
            # noticing missing chat bubbles for legacy artifacts have
            # something to grep, instead of failing silently.
            print(
                f"[chat_log] orphan {json_path.name} skipped: "
                "no baseline_iters in outcome JSON",
                file=sys.stderr, flush=True,
            )
            continue
        try:
            baseline_iters = sorted({int(x) for x in raw_iters})
        except (TypeError, ValueError):
            continue
        sid = compute_set_id(baseline_iters)
        if set_id is not None and sid != set_id:
            continue
        if (
            in_terminal
            and allowed_terminal_sids is not None
            and sid not in allowed_terminal_sids
        ):
            # Terminal-state run with a recorded refine session for SOME
            # set_id, but not THIS one — leave the unrelated stale
            # artifact pair alone.
            continue
        if (sid, refine_idx) in seen:
            continue
        review = outcome.get("review") or "Recovered completed refinement."
        rcparams_delta = outcome.get("rcparams_delta") or {}
        entry = append_turn(
            workdir,
            role="assistant",
            content=review,
            set_id=sid,
            baseline_iters=baseline_iters,
            image_url=f"refine_{refine_idx:03d}.png",
            rcparams_delta=rcparams_delta,
            review=review,
            refine_idx=refine_idx,
            recovered=True,
        )
        recovered.append(entry)
        seen.add((sid, refine_idx))
        if in_terminal:
            consumed_terminal_pairs.add((sid, refine_idx))
    # PR #25 round-3 finding #2 + round-4 finding #1: on the terminal
    # path, clear the marker for each consumed orphan ONLY when the
    # marker's stamped ``refine_idx`` matches the consumed orphan's
    # index. Round-3 fix (clear after consume) prevents a one-time
    # carve-out from becoming a permanent bypass; round-4 refinement
    # (index-aware clear) prevents an OLDER same-set orphan from
    # clearing the LIVE runner's marker. The runner's own ``finally``
    # remains the canonical clear path for the active turn — recovery
    # only finalises markers whose owning runner already crashed
    # (matching index, or pre-round-4 marker with no index stamp).
    for sid, idx in consumed_terminal_pairs:
        clear_refine_in_flight_for_idx(workdir, sid, idx)
    return recovered


def list_set_ids(workdir: Path) -> list[dict]:
    """List distinct ``set_id`` values with metadata.

    Returns a list of dicts ``{set_id, baseline_iters, turn_count,
    last_ts}``, sorted by ``last_ts`` descending (most-recent chat
    first). Used by the ``GET /api/runs/<name>/chats`` endpoint.

    Implementation note: ``turn_count`` counts **assistant** entries
    (= "how many refines have completed in this chat"), since user
    entries can interleave (e.g. message + a few adjustments) without
    necessarily producing a refine output.
    """
    recover_orphan_refines(workdir)
    entries = read_turns(workdir)
    by_set: dict[str, dict] = {}
    for entry in entries:
        sid = entry.get("set_id")
        if not isinstance(sid, str):
            continue
        bucket = by_set.setdefault(sid, {
            "set_id": sid,
            "baseline_iters": list(entry.get("baseline_iters") or []),
            "turn_count": 0,
            "last_ts": entry.get("ts", ""),
        })
        if entry.get("role") == "assistant":
            bucket["turn_count"] += 1
        ts = entry.get("ts", "")
        if isinstance(ts, str) and ts > bucket["last_ts"]:
            bucket["last_ts"] = ts
    return sorted(
        by_set.values(),
        key=lambda b: b.get("last_ts", ""),
        reverse=True,
    )
