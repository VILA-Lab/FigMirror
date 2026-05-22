"""chat_log: append + filter-by-set_id + atomic-write discipline."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from figcopy_runner import chat_log
from figcopy_runner.interface import (
    active_refine_in_flight_set_ids,
    clear_refine_in_flight,
    clear_refine_in_flight_for_idx,
    clear_refine_reservation,
    compute_set_id,
    mark_refine_in_flight,
    next_refine_index,
    reserve_refine_index,
)


def test_append_creates_file(tmp_path):
    entry = chat_log.append_turn(
        tmp_path, role="user", content="hi",
        set_id="abc12345", baseline_iters=[1, 3],
    )
    path = tmp_path / "chat.jsonl"
    assert path.exists()
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["role"] == "user"
    assert parsed["content"] == "hi"
    assert parsed["set_id"] == "abc12345"
    assert parsed["baseline_iters"] == [1, 3]
    assert "ts" in parsed
    assert entry == parsed


def test_append_multiple_turns_preserves_order(tmp_path):
    for content in ("a", "b", "c", "d"):
        chat_log.append_turn(
            tmp_path, role="user", content=content,
            set_id="s1", baseline_iters=[1],
        )
    entries = chat_log.read_turns(tmp_path)
    assert [e["content"] for e in entries] == ["a", "b", "c", "d"]


def test_filter_by_set_id(tmp_path):
    chat_log.append_turn(tmp_path, role="user", content="for-A",
                         set_id="A", baseline_iters=[1])
    chat_log.append_turn(tmp_path, role="user", content="for-B",
                         set_id="B", baseline_iters=[2])
    chat_log.append_turn(tmp_path, role="assistant", content="reply-A",
                         set_id="A", baseline_iters=[1])

    only_a = chat_log.read_turns(tmp_path, set_id="A")
    assert [e["content"] for e in only_a] == ["for-A", "reply-A"]

    only_b = chat_log.read_turns(tmp_path, set_id="B")
    assert [e["content"] for e in only_b] == ["for-B"]

    all_turns = chat_log.read_turns(tmp_path)
    assert len(all_turns) == 3


def test_extras_are_preserved(tmp_path):
    chat_log.append_turn(
        tmp_path, role="assistant", content="ok",
        set_id="X", baseline_iters=[1, 2],
        image_url="refine_001.png",
        rcparams_delta={"font.size": 13},
        review="bumped font",
        refine_idx=1,
        seq=42,
    )
    entries = chat_log.read_turns(tmp_path)
    e = entries[0]
    assert e["image_url"] == "refine_001.png"
    assert e["rcparams_delta"] == {"font.size": 13}
    assert e["review"] == "bumped font"
    assert e["refine_idx"] == 1
    assert e["seq"] == 42


def test_read_missing_file(tmp_path):
    assert chat_log.read_turns(tmp_path) == []
    assert chat_log.read_turns(tmp_path, set_id="anything") == []


def test_read_skips_malformed_lines(tmp_path):
    path = tmp_path / "chat.jsonl"
    # First line valid, second line garbage, third valid.
    path.write_text(
        json.dumps({"role": "user", "content": "ok", "set_id": "x"})
        + "\nNOT-JSON\n"
        + json.dumps({"role": "assistant", "content": "fine", "set_id": "x"})
        + "\n"
    )
    entries = chat_log.read_turns(tmp_path)
    assert [e["content"] for e in entries] == ["ok", "fine"]


def test_atomic_write_no_tmp_left_behind(tmp_path):
    """After successful append, the .tmp shadow file MUST NOT exist."""
    chat_log.append_turn(tmp_path, role="user", content="hi",
                         set_id="s", baseline_iters=[1])
    tmp = tmp_path / "chat.jsonl.tmp"
    assert not tmp.exists()


def test_recover_orphan_refines_appends_missing_assistant(tmp_path):
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [5],
        "rcparams_delta": {"axes.facecolor": "#f8fafc"},
        "review": "Recovered result",
    }))

    recovered = chat_log.recover_orphan_refines(tmp_path)

    set_id = compute_set_id([5])
    assert len(recovered) == 1
    entry = recovered[0]
    assert entry["role"] == "assistant"
    assert entry["set_id"] == set_id
    assert entry["baseline_iters"] == [5]
    assert entry["image_url"] == "refine_001.png"
    assert entry["refine_idx"] == 1
    assert entry["recovered"] is True

    assert chat_log.recover_orphan_refines(tmp_path) == []
    turns = chat_log.read_turns(tmp_path, set_id=set_id)
    assert len(turns) == 1


def test_recover_orphan_refines_respects_set_filter(tmp_path):
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [5],
        "review": "ok",
    }))

    assert chat_log.recover_orphan_refines(
        tmp_path, set_id=compute_set_id([2]),
    ) == []
    assert chat_log.read_turns(tmp_path) == []


def test_list_set_ids(tmp_path):
    # Two chats: set "A" with 2 user + 1 assistant; set "B" with 1 of each.
    chat_log.append_turn(tmp_path, role="user", content="ask1",
                         set_id="A", baseline_iters=[1, 3])
    chat_log.append_turn(tmp_path, role="assistant", content="reply1",
                         set_id="A", baseline_iters=[1, 3])
    chat_log.append_turn(tmp_path, role="user", content="ask2",
                         set_id="A", baseline_iters=[1, 3])
    chat_log.append_turn(tmp_path, role="user", content="b-ask",
                         set_id="B", baseline_iters=[2])
    chat_log.append_turn(tmp_path, role="assistant", content="b-reply",
                         set_id="B", baseline_iters=[2])

    listing = chat_log.list_set_ids(tmp_path)
    by_sid = {e["set_id"]: e for e in listing}
    assert set(by_sid) == {"A", "B"}
    # turn_count = number of ASSISTANT entries per spec D4's count
    # semantics. A has 1 assistant, B has 1 assistant.
    assert by_sid["A"]["turn_count"] == 1
    assert by_sid["B"]["turn_count"] == 1
    assert by_sid["A"]["baseline_iters"] == [1, 3]
    assert by_sid["B"]["baseline_iters"] == [2]


# ─── round-4 regressions ──────────────────────────────────────────────


@pytest.mark.parametrize("terminal_state", ["shipped", "failed", "cancelled"])
def test_recover_orphan_refines_skips_terminal_runs(tmp_path, terminal_state):
    """Round-4 regression mirroring the round-2 ``_TERMINAL_RUN_STATUSES``
    invariant in ``figcopy_serve``: a terminal run is read-only
    browsable. Stale orphan ``refine_NNN.{png,json}`` artifacts on
    disk MUST NOT cause ``chat.jsonl`` to mutate on a page-load /
    chat-history GET. Otherwise ``GET /r/<name>`` (and
    ``GET /api/runs/<name>/chat/<set_id>``) would silently append a
    "Recovered completed refinement." bubble to a chat the user
    already considers closed. Parametrised over all three terminal
    states so a future regression on any of them fails."""
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [5],
        "review": "would-be recovered",
    }))
    # Capture a snapshot of chat.jsonl pre-recovery so we can assert
    # zero mutation. (No file → empty contents.)
    chat_path = tmp_path / "chat.jsonl"
    pre_existed = chat_path.exists()
    pre_contents = chat_path.read_bytes() if pre_existed else None

    # Stage a terminal status sidecar.
    (tmp_path / "status.json").write_text(json.dumps({
        "state": terminal_state,
    }))

    assert chat_log.recover_orphan_refines(tmp_path) == []
    # File must not have been created or modified.
    assert chat_path.exists() == pre_existed
    if pre_existed:
        assert chat_path.read_bytes() == pre_contents
    # And reading turns still surfaces nothing — confirms no in-place
    # mutation slipped past the byte comparison.
    assert chat_log.read_turns(tmp_path) == []


def test_recover_orphan_refines_skips_terminal_runs_via_list_set_ids(tmp_path):
    """``list_set_ids`` calls ``recover_orphan_refines`` internally —
    pin that the terminal-status gate also covers that call site, not
    just direct callers."""
    (tmp_path / "refine_002.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_002.json").write_text(json.dumps({
        "baseline_iters": [3],
        "review": "would-be recovered",
    }))
    (tmp_path / "status.json").write_text(json.dumps({"state": "shipped"}))

    listing = chat_log.list_set_ids(tmp_path)
    # No chat exists; the orphan must NOT have been backfilled.
    assert listing == []
    assert not (tmp_path / "chat.jsonl").exists()


def test_recover_orphan_refines_runs_when_status_running(tmp_path):
    """Inverse of the terminal-status gate: a run still marked
    ``running`` (or with no sidecar at all) MUST still recover. This
    pins that the gate fires only on the documented terminal set —
    a typo widening the set would silently disable recovery for
    in-flight runs and regress the original feature."""
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [5],
        "review": "ok",
    }))
    (tmp_path / "status.json").write_text(json.dumps({"state": "running"}))

    recovered = chat_log.recover_orphan_refines(tmp_path)
    assert len(recovered) == 1
    assert recovered[0]["refine_idx"] == 1


def test_append_turn_dedups_assistant_refine_idx_within_set_id(tmp_path):
    """Round-4 regression: the duplicate-assistant-turn race between
    the runner-side ``append_turn`` (in ``claude.py`` /
    ``codex.py:_refine_locked``) and the recovery-path
    ``append_turn`` (in ``recover_orphan_refines``). The two writers
    can interleave in either order — whichever loses the race must
    skip its append, NOT clobber it. Pinning idempotency on
    ``(set_id, refine_idx)`` for assistant entries closes both
    directions: the fix is symmetric.

    Direction A: recovery wins, runner runs second.
    """
    set_id = compute_set_id([5])
    # Recovery-path append lands first (the page-load reached
    # ``recover_orphan_refines`` while the runner's ``append_turn``
    # call was still in flight).
    chat_log.append_turn(
        tmp_path, role="assistant", content="recovered",
        set_id=set_id, baseline_iters=[5],
        image_url="refine_001.png", refine_idx=1,
        recovered=True,
    )
    # Runner-side append now arrives — same (set_id, refine_idx).
    # Must be a no-op; returns the prior entry instead.
    second = chat_log.append_turn(
        tmp_path, role="assistant", content="from runner",
        set_id=set_id, baseline_iters=[5],
        image_url="refine_001.png", refine_idx=1, seq=42,
    )
    assert second.get("recovered") is True
    assert second.get("seq") is None  # the prior entry, not the runner's

    turns = chat_log.read_turns(tmp_path, set_id=set_id)
    assert len(turns) == 1, (
        "duplicate assistant bubble for the same refine_idx — "
        "race window between runner.append_turn and "
        "recover_orphan_refines was not closed"
    )


def test_append_turn_dedups_when_runner_already_appended(tmp_path):
    """Direction B of the race: the runner-side append wins; a later
    ``recover_orphan_refines`` pass on a concurrent GET must NOT
    re-append. The existing ``seen``-set logic in
    ``recover_orphan_refines`` already handles this for the recovery
    path itself; the deduper inside ``append_turn`` is a defense in
    depth that also catches the case where a regression drops the
    ``refine_idx`` extra from the recovery path."""
    set_id = compute_set_id([5])
    # Runner-side append lands first.
    chat_log.append_turn(
        tmp_path, role="assistant", content="from runner",
        set_id=set_id, baseline_iters=[5],
        image_url="refine_001.png", refine_idx=1, seq=42,
    )
    # A direct second call (e.g. from a recovery path that lost the
    # seen-set check due to a regression) must be skipped.
    second = chat_log.append_turn(
        tmp_path, role="assistant", content="recovered",
        set_id=set_id, baseline_iters=[5],
        image_url="refine_001.png", refine_idx=1,
        recovered=True,
    )
    # The returned entry is the prior runner-side one, not the new
    # recovered shape.
    assert second.get("seq") == 42
    assert second.get("recovered") is None

    turns = chat_log.read_turns(tmp_path, set_id=set_id)
    assert len(turns) == 1


def test_append_turn_does_not_dedup_user_or_freeform_assistant(tmp_path):
    """Idempotency is intentionally narrow: only assistant entries
    that carry a ``refine_idx`` (i.e., refine-completion bubbles) are
    deduped. User turns and assistant free-text replies may
    legitimately repeat — collapsing them would lose chat content.
    Pin both negative cases."""
    set_id = compute_set_id([5])
    # Two identical user turns — both must land.
    chat_log.append_turn(
        tmp_path, role="user", content="hi",
        set_id=set_id, baseline_iters=[5],
    )
    chat_log.append_turn(
        tmp_path, role="user", content="hi",
        set_id=set_id, baseline_iters=[5],
    )
    # Two identical assistant text replies (no refine_idx) — both must land.
    chat_log.append_turn(
        tmp_path, role="assistant", content="ack",
        set_id=set_id, baseline_iters=[5],
    )
    chat_log.append_turn(
        tmp_path, role="assistant", content="ack",
        set_id=set_id, baseline_iters=[5],
    )

    turns = chat_log.read_turns(tmp_path, set_id=set_id)
    assert len(turns) == 4


def test_recover_orphan_refines_logs_skipped_legacy_outcome(
    tmp_path, capsys,
):
    """Round-4 advisory follow-up: legacy refine outputs written
    before the round-4 commit may not embed ``baseline_iters``. The
    recovery path cannot route them without it, so they are silently
    skipped — surface a stderr line so operators noticing missing
    chat bubbles for legacy artifacts have something to grep for."""
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        # No baseline_iters key — pre-PR shape.
        "review": "legacy",
    }))

    assert chat_log.recover_orphan_refines(tmp_path) == []
    err = capsys.readouterr().err
    assert "refine_001.json" in err
    assert "no baseline_iters" in err


# ─── PR #25 round-1 regressions ───────────────────────────────────────


@pytest.mark.parametrize("terminal_state", ["shipped", "failed", "cancelled"])
def test_recover_orphan_refines_carve_out_for_active_refine_session_on_terminal_run(
    tmp_path, terminal_state,
):
    """PR #25 round-1 finding #1: the canonical Step-2 user flow is
    refining on an already-shipped run. The runner stamps a per-set_id
    in-flight marker BEFORE spawning the long-running CLI subprocess
    (PR #25 round-2 follow-up replaced the prior ``sessions.json``
    lookup with a true write-at-entry/clear-at-exit marker). If the
    HTTP request dies after the artifacts land but before the runner's
    own ``append_turn`` call, the recovery path is the only thing that
    surfaces the completed refine to the user. The pre-fix
    terminal-status gate silently swallowed it. Pin that the carve-out
    fires only for set_ids the runner has actually marked in-flight —
    unrelated stale artifact pairs MUST still be skipped (preserves
    the round-4 invariant for them)."""
    set_id = compute_set_id([5])
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [5],
        "rcparams_delta": {"axes.facecolor": "#f8fafc"},
        "review": "Recovered after shipped",
    }))
    # Simulate the runner having stamped the in-flight marker BEFORE
    # the request died (PR #25 round-2 fix replaces the old
    # sessions.json["refine"] dependency).
    mark_refine_in_flight(tmp_path, set_id)
    # Run is in a terminal state — under the pre-fix gate this would
    # have silently dropped the recovery.
    (tmp_path / "status.json").write_text(json.dumps({
        "state": terminal_state,
    }))

    recovered = chat_log.recover_orphan_refines(tmp_path)
    assert len(recovered) == 1, (
        "PR #25 round-1 finding #1: recovery on a terminal run with "
        "an in-flight refine marker for the orphan's set_id MUST "
        "surface the recovered turn — otherwise users lose completed "
        "Step-2 refines whose POST died after artifacts landed."
    )
    assert recovered[0]["set_id"] == set_id
    assert recovered[0]["refine_idx"] == 1


@pytest.mark.parametrize("terminal_state", ["shipped", "failed", "cancelled"])
def test_recover_orphan_refines_terminal_carve_out_only_for_registered_set_ids(
    tmp_path, terminal_state,
):
    """The carve-out is narrow: it fires ONLY for set_ids the runner
    marked as in-flight. An unrelated stale artifact pair on disk
    (different set_id, no in-flight marker) MUST still be skipped on
    a terminal run — same invariant as the existing
    ``test_recover_orphan_refines_skips_terminal_runs`` parametrise,
    but now in the presence of an in-flight marker for a DIFFERENT
    set_id."""
    registered_sid = compute_set_id([5])
    other_sid = compute_set_id([7])
    # Stale orphan for set_id=other_sid (NOT in-flight).
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [7],  # → other_sid
        "review": "stale",
    }))
    mark_refine_in_flight(tmp_path, registered_sid)
    (tmp_path / "status.json").write_text(json.dumps({
        "state": terminal_state,
    }))

    # No filter passed → recovery loop runs (carve-out is non-empty),
    # but the unrelated set_id is filtered out inside the loop.
    assert chat_log.recover_orphan_refines(tmp_path) == []
    # Direct set_id filter on the unregistered sid → early-return.
    assert chat_log.recover_orphan_refines(
        tmp_path, set_id=other_sid,
    ) == []
    # The chat.jsonl must not have been mutated.
    assert not (tmp_path / "chat.jsonl").exists()


def test_recover_orphan_refines_terminal_no_sessions_json_still_gated(tmp_path):
    """Belt-and-suspenders: a terminal run with no in-flight markers
    on disk MUST behave exactly like the pre-fix gate — skip everything.
    ``active_refine_in_flight_set_ids`` returns an empty set, so the
    carve-out collapses to the original "skip on terminal" behavior."""
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [5],
        "review": "stale",
    }))
    (tmp_path / "status.json").write_text(json.dumps({"state": "shipped"}))
    # No in-flight markers on disk.
    assert chat_log.recover_orphan_refines(tmp_path) == []
    assert not (tmp_path / "chat.jsonl").exists()


# ─── PR #25 round-2 regressions ───────────────────────────────────────


@pytest.mark.parametrize("terminal_state", ["shipped", "failed", "cancelled"])
def test_recover_orphan_refines_early_crash_variant(tmp_path, terminal_state):
    """PR #25 round-2 finding #1 (early-crash variant): the prior
    carve-out keyed on ``sessions["refine"][set_id]``, but that key is
    written by the runner AFTER the subprocess completes
    (claude.py:462 / codex.py:754) and only when the agent emitted a
    ``session_id`` frame. If the subprocess writes
    ``refine_NNN.{png,json}`` and then SIGKILL's BEFORE the runner
    persists the session id, ``sessions["refine"]`` never gets the
    entry → the pre-fix carve-out silently swallows the artifacts on
    a terminal-state run.

    The in-flight-marker approach (write at TOP of ``_refine_locked``
    BEFORE subprocess spawn, clear in ``finally`` after
    ``append_turn``) closes this. Pin that artifacts written by an
    early-crashing subprocess on a terminal-state run are still
    recoverable, even when ``sessions.json`` has no entry for the
    set_id at all."""
    set_id = compute_set_id([5])
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [5],
        "rcparams_delta": {"font.size": 13},
        "review": "Recovered after early subprocess crash",
    }))
    # Simulate the runner having stamped the in-flight marker BEFORE
    # subprocess spawn — the subprocess wrote artifacts then SIGKILL'd
    # before reaching the persist-session-id step. Crucially, NO
    # sessions.json on disk: the early crash never reached it.
    mark_refine_in_flight(tmp_path, set_id)
    assert not (tmp_path / "sessions.json").exists(), (
        "test setup invariant: this test exercises the early-crash "
        "variant where sessions.json is absent (the runner died "
        "before write_sessions)."
    )
    (tmp_path / "status.json").write_text(json.dumps({"state": terminal_state}))

    recovered = chat_log.recover_orphan_refines(tmp_path)
    assert len(recovered) == 1, (
        "PR #25 round-2 finding #1: in-flight marker decouples the "
        "carve-out from sessions.json — artifacts from a subprocess "
        "that crashed before write_sessions must still be recoverable."
    )
    assert recovered[0]["set_id"] == set_id


def test_recover_orphan_refines_stale_session_key_does_not_widen_carve_out(
    tmp_path,
):
    """PR #25 round-2 finding #1 (stale-key variant): ``sessions["refine"]``
    has no ``pop``/``del`` callsite — entries from prior completed
    turns persist forever. Under the pre-fix carve-out, a stale entry
    would permanently bypass the round-2 invariant for any same-set
    artifact pair that lands later via out-of-band tooling.

    The in-flight-marker approach fixes this: if no marker is on disk
    (because the prior turn cleared it in its ``finally``), the
    carve-out is closed even when ``sessions.json["refine"]`` still
    has the set_id. Pin the invariant."""
    set_id = compute_set_id([5])
    # An artifact pair lands later via out-of-band tooling.
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [5],
        "review": "out-of-band stale",
    }))
    # ``sessions.json`` retains a stale refine session id from a
    # prior completed turn (the entry is never removed).
    (tmp_path / "sessions.json").write_text(json.dumps({
        "iter": None,
        "refine": {set_id: "stale-prior-turn-session-uuid"},
    }))
    # No in-flight marker — the prior turn cleared it.
    assert active_refine_in_flight_set_ids(tmp_path) == set()
    (tmp_path / "status.json").write_text(json.dumps({"state": "shipped"}))

    assert chat_log.recover_orphan_refines(tmp_path) == [], (
        "PR #25 round-2 finding #1: a stale ``sessions['refine']`` "
        "entry MUST NOT widen the carve-out for out-of-band artifacts "
        "on a terminal run. Only an in-flight marker grants the "
        "carve-out."
    )
    assert not (tmp_path / "chat.jsonl").exists()


def test_inflight_marker_clear_is_idempotent_and_does_not_raise(tmp_path):
    """``clear_refine_in_flight`` runs in a ``finally`` and must
    tolerate a missing marker (e.g., disk error during the matching
    ``mark_refine_in_flight``, or a partial crash that already lost
    the file). Pin the contract so a regression that turns the
    cleanup into a hard error doesn't mask the real turn outcome."""
    set_id = "deadbeef"
    # No marker on disk → must not raise.
    clear_refine_in_flight(tmp_path, set_id)

    mark_refine_in_flight(tmp_path, set_id)
    assert active_refine_in_flight_set_ids(tmp_path) == {set_id}
    clear_refine_in_flight(tmp_path, set_id)
    assert active_refine_in_flight_set_ids(tmp_path) == set()
    # Clearing again is a no-op.
    clear_refine_in_flight(tmp_path, set_id)
    assert active_refine_in_flight_set_ids(tmp_path) == set()


# ─── PR #25 round-3 regressions ───────────────────────────────────────


def test_inflight_marker_payload_carries_pid_and_start_ts(tmp_path):
    """PR #25 round-3 finding #2: each marker file MUST carry the
    writing process's pid and a monotonic timestamp so readers can
    detect markers whose owning runner died (host OOM / SIGKILL /
    power loss between mark and finally). Pin the on-disk shape so a
    regression that drops the JSON envelope (back to raw set_id) is
    caught immediately."""
    import json as _json
    import os as _os

    set_id = "deadbeef"
    mark_refine_in_flight(tmp_path, set_id)
    marker = tmp_path / f".refine_inflight_{set_id}"
    assert marker.is_file()

    payload = _json.loads(marker.read_text(encoding="utf-8"))
    assert payload["pid"] == _os.getpid()
    assert payload["set_id"] == set_id
    # start_ts is an integer monotonic_ns reading; just sanity-check
    # the type — the value itself depends on host uptime.
    assert isinstance(payload["start_ts"], int)
    assert payload["start_ts"] > 0


def test_active_refine_set_ids_ignores_marker_for_dead_pid(tmp_path):
    """PR #25 round-3 finding #2: a marker stamped by a process that
    has since died (host OOM / SIGKILL / power-loss) MUST NOT widen
    the carve-out forever. ``active_refine_in_flight_set_ids`` ignores
    markers whose stamped pid is no longer alive on this host.

    We synthesize a dead-pid marker by writing one with pid=1 but for
    a fake set_id, then... we can't actually be sure pid=1 is dead.
    Instead, fork a child and capture its pid; reap it; write the
    marker with that (now-dead) pid; assert it's filtered."""
    import json as _json
    import os as _os
    import subprocess as _sp

    set_id = "deadbeef"

    # Spawn-and-immediately-reap a short-lived process so we get a pid
    # that is guaranteed to be dead by the time we check it.
    proc = _sp.Popen(["true"])
    proc.wait()
    dead_pid = proc.pid
    # Sanity: the pid must actually be dead on this host. If the OS
    # reused it within the few ms between Popen and now, skip the
    # test rather than emit a flake.
    try:
        _os.kill(dead_pid, 0)
    except ProcessLookupError:
        pass
    else:
        pytest.skip("pid was reused before the assertion; rerun")

    marker = tmp_path / f".refine_inflight_{set_id}"
    marker.write_text(_json.dumps({
        "pid": dead_pid,
        "start_ts": 0,
        "set_id": set_id,
    }) + "\n", encoding="utf-8")

    # The marker exists on disk, but the recorded pid is dead → the
    # carve-out lookup must filter it out.
    assert active_refine_in_flight_set_ids(tmp_path) == set(), (
        "PR #25 round-3 finding #2: dead-pid markers MUST be filtered "
        "out so a SIGKILL'd runner doesn't wedge the carve-out forever."
    )


def test_active_refine_set_ids_keeps_marker_for_live_pid(tmp_path):
    """Symmetric to the dead-pid test: a marker stamped by a live
    process MUST stay surfaced. The default ``mark_refine_in_flight``
    uses ``os.getpid()`` so the test process IS that live pid."""
    set_id = "deadbeef"
    mark_refine_in_flight(tmp_path, set_id)
    assert active_refine_in_flight_set_ids(tmp_path) == {set_id}


def test_active_refine_set_ids_treats_legacy_marker_as_alive(tmp_path):
    """Backward compatibility: a marker file whose payload is the raw
    ``set_id`` (the round-2 shape, no JSON envelope) is treated as
    alive — we cannot read a pid from it, so the safe behavior is to
    keep the carve-out for the in-flight runner that wrote it. This
    matters during an in-place upgrade across releases when the new
    code starts while a runner from the old code is still in flight."""
    set_id = "abadcafe"
    marker = tmp_path / f".refine_inflight_{set_id}"
    marker.write_text(set_id + "\n", encoding="utf-8")
    assert active_refine_in_flight_set_ids(tmp_path) == {set_id}


def test_active_refine_set_ids_skips_tmp_rename_intermediate(tmp_path):
    """PR #25 round-3 advisory: ``atomic_write_text`` writes
    ``.refine_inflight_<sid>.tmp`` then renames; the glob in
    ``active_refine_in_flight_set_ids`` would otherwise match that
    intermediate file during the rename window and surface a phantom
    ``"<sid>.tmp"`` set element. Pin that the ``.tmp`` suffix is
    filtered so concurrent readers under load don't see polluted
    set ids."""
    sid = "deadbeef"
    # Real marker for this sid (live pid → kept).
    mark_refine_in_flight(tmp_path, sid)
    # Synthetic .tmp rename intermediate that the glob would otherwise
    # pick up.
    (tmp_path / ".refine_inflight_cafef00d.tmp").write_text("noise")
    out = active_refine_in_flight_set_ids(tmp_path)
    assert out == {sid}, (
        "active_refine_in_flight_set_ids must skip .tmp rename "
        "intermediates produced by atomic_write_text — otherwise the "
        "returned set is polluted with semantically-wrong values."
    )


def test_active_refine_set_ids_skips_invalid_set_id_shape(tmp_path):
    """Defense in depth: a marker filename whose suffix is NOT a
    valid 8-hex set_id (e.g., manually placed by an operator, or a
    leftover from a future version with a different shape) MUST be
    skipped. Otherwise it would widen the carve-out for an arbitrary
    string."""
    (tmp_path / ".refine_inflight_NOT_VALID").write_text("noise")
    assert active_refine_in_flight_set_ids(tmp_path) == set()


def test_mark_refine_in_flight_rejects_bad_set_id(tmp_path):
    """PR #25 round-3 advisory: ``mark_refine_in_flight`` validates
    ``set_id`` shape so a future caller wiring user-controlled input
    through this surface cannot escape the workdir via path-traversal
    in the marker filename. All current callers pass
    ``compute_set_id`` output (8 lowercase-hex chars).

    Pin the validation contract by exercising the four common
    attack/typo shapes."""
    for bad in [
        "../escape",        # traversal
        "with/slash",       # subdir
        "DEADBEEF",         # uppercase (compute_set_id is lowercase)
        "deadbee",          # too short
        "deadbeefdeadbeef", # too long
        "",                 # empty
    ]:
        with pytest.raises(ValueError):
            mark_refine_in_flight(tmp_path, bad)
        with pytest.raises(ValueError):
            clear_refine_in_flight(tmp_path, bad)


@pytest.mark.parametrize("terminal_state", ["shipped", "failed", "cancelled"])
def test_recover_orphan_refines_clears_marker_after_consumption(
    tmp_path, terminal_state,
):
    """PR #25 round-3 finding #2 (recovery clears consumed markers):
    on a terminal-state run, after recovery surfaces an orphan pair
    via the in-flight-marker carve-out, the marker MUST be cleared so
    the carve-out doesn't permanently bypass the round-2 invariant
    for that set_id. Without this, a SUBSEQUENT out-of-band artifact
    pair under the same set_id (out-of-band tooling, partial-write
    recovery, a different runner re-stamping after consumption) would
    still be silently surfaced via the still-live marker.

    Sequence pinned: mark → land artifact pair #1 → terminal state →
    recovery surfaces #1 + clears marker → land out-of-band artifact
    pair #2 (same set_id) → recovery refuses to surface #2."""
    set_id = compute_set_id([5])
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [5],
        "review": "first turn — runner crashed before append_turn",
    }))
    mark_refine_in_flight(tmp_path, set_id)
    (tmp_path / "status.json").write_text(json.dumps({
        "state": terminal_state,
    }))

    # First recovery: marker is live → surface the orphan.
    first = chat_log.recover_orphan_refines(tmp_path)
    assert len(first) == 1
    # Marker MUST have been cleared by the recovery.
    assert active_refine_in_flight_set_ids(tmp_path) == set(), (
        "PR #25 round-3 finding #2: recover_orphan_refines must clear "
        "the in-flight marker for any consumed set_id on a terminal "
        "run, otherwise the carve-out becomes a permanent bypass."
    )

    # Second recovery: out-of-band tooling lands a NEW artifact pair
    # under the same set_id. Marker is gone → terminal-status gate
    # closes again → recovery refuses.
    (tmp_path / "refine_002.png").write_bytes(b"\x89PNG\r\nfake2")
    (tmp_path / "refine_002.json").write_text(json.dumps({
        "baseline_iters": [5],
        "review": "second pair — out-of-band, must NOT be surfaced",
    }))
    second = chat_log.recover_orphan_refines(tmp_path)
    assert second == [], (
        "PR #25 round-3 finding #2: a second out-of-band artifact "
        "pair landing under the same set_id after the marker has "
        "been consumed MUST be skipped on a terminal-state run "
        "(no live in-flight marker → no carve-out)."
    )


def test_recover_orphan_refines_does_not_clear_marker_on_active_run(tmp_path):
    """The marker-clear is bounded to the terminal path. On a
    ``running`` (non-terminal) run, the runner's ``_refine_locked``
    finally is the canonical owner of the marker lifecycle; if
    recovery cleared it from under an active runner, the runner's
    own clear would then no-op (fine — the clear is idempotent) but
    if a CONCURRENT recovery + another runner racing to re-stamp
    happened, the second runner's marker could be wiped before its
    finally fires. Pin the safe behavior: do NOT clear on non-terminal
    runs."""
    set_id = compute_set_id([5])
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [5],
        "review": "active-run recovery",
    }))
    mark_refine_in_flight(tmp_path, set_id)
    (tmp_path / "status.json").write_text(json.dumps({"state": "running"}))

    recovered = chat_log.recover_orphan_refines(tmp_path)
    assert len(recovered) == 1
    # Marker MUST still be present — the runner's own finally owns it.
    assert active_refine_in_flight_set_ids(tmp_path) == {set_id}


# ─── PR #25 round-4 regressions ───────────────────────────────────────


@pytest.mark.parametrize("terminal_state", ["shipped", "failed", "cancelled"])
def test_recover_orphan_refines_does_not_clear_live_marker_for_other_refine_idx(
    tmp_path, terminal_state,
):
    """PR #25 round-4 finding #1: the carve-out's whole point is to
    let Step-2 refines fire WHILE ``status.json["state"]`` is
    terminal. So ``in_terminal=True`` is precisely the case where a
    runner CAN be live. Recovery must NOT clear the live runner's
    marker just because it consumed an OLDER same-set orphan from a
    prior crashed turn.

    Bug-trigger sequence pinned (would have hit the round-3 code):

    1. Prior turn at refine_idx=1 crashed mid-write — left
       ``refine_001.{png,json}`` on disk but never appended chat.
    2. Run shipped (status.json terminal).
    3. User asks for ANOTHER refine on the same baseline set →
       active runner stamps the marker for refine_idx=2.
    4. Page-load HTTP handler calls ``recover_orphan_refines``.
       Round-3 behaviour: sees in_terminal=True + live marker for
       set_id, surfaces the orphan (refine_001), clears the marker.
       This wipes the LIVE runner's marker.
    5. The active runner crashes mid-render. Its ``finally`` clear
       is a no-op (already gone). Next page-load recovery: active
       runner's now-orphaned refine_002 artifacts are NOT carved
       out → user loses the just-completed refine.

    Round-4 fix: marker is bound to refine_idx=2; recovery refuses
    to clear when the consumed orphan's index (1) doesn't match.
    Active runner's marker survives."""
    set_id = compute_set_id([5])
    # OLDER orphan (refine_001) from a prior crashed turn.
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nold")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [5],
        "review": "older-turn orphan",
    }))
    # ACTIVE runner stamps marker for refine_idx=2 (in-flight).
    mark_refine_in_flight(tmp_path, set_id, refine_idx=2)
    (tmp_path / "status.json").write_text(json.dumps({"state": terminal_state}))

    # Page-load recovery runs. It SHOULD surface refine_001 (the
    # carve-out is for in-flight set_ids, regardless of which orphan
    # actually exists), but it MUST NOT clear the marker that's
    # bound to refine_idx=2.
    recovered = chat_log.recover_orphan_refines(tmp_path)
    assert len(recovered) == 1
    assert recovered[0]["refine_idx"] == 1, (
        "carve-out surfaced the older orphan as expected"
    )
    assert active_refine_in_flight_set_ids(tmp_path) == {set_id}, (
        "PR #25 round-4 finding #1: recovery must NOT clear the "
        "live runner's marker just because it consumed an older "
        "same-set orphan with a different refine_idx. Otherwise the "
        "active runner's own crash would lose its carve-out."
    )


@pytest.mark.parametrize("terminal_state", ["shipped", "failed", "cancelled"])
def test_recover_orphan_refines_clears_marker_when_refine_idx_matches(
    tmp_path, terminal_state,
):
    """Sister test to the round-4 fix: when the consumed orphan's
    refine_idx DOES match the marker's stamped index, the marker
    must still be cleared (round-3 invariant — one-shot carve-out).
    This is the canonical case: a runner stamps the marker for
    refine_idx=N, the subprocess writes refine_NNN.{png,json}, the
    runner crashes before append_turn, recovery surfaces the
    orphan, marker is cleared so a SUBSEQUENT out-of-band pair under
    the same set_id doesn't re-trigger the carve-out."""
    set_id = compute_set_id([5])
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [5],
        "review": "in-flight orphan, runner crashed mid-write",
    }))
    # Marker bound to refine_idx=1 — matches the orphan we'll consume.
    mark_refine_in_flight(tmp_path, set_id, refine_idx=1)
    (tmp_path / "status.json").write_text(json.dumps({"state": terminal_state}))

    recovered = chat_log.recover_orphan_refines(tmp_path)
    assert len(recovered) == 1
    assert recovered[0]["refine_idx"] == 1
    # Index matched → marker IS cleared (round-3 invariant restored
    # post-round-4-fix).
    assert active_refine_in_flight_set_ids(tmp_path) == set(), (
        "PR #25 round-3 invariant must still hold when the consumed "
        "orphan's refine_idx matches the marker's stamped index."
    )


def test_recover_orphan_refines_legacy_marker_without_refine_idx_still_cleared(
    tmp_path,
):
    """Backward compatibility: a JSON marker that's missing the
    round-4 ``refine_idx`` field (e.g., written by a runner from the
    last release that's still in flight when the new code starts)
    must still get cleared after consumption — otherwise the round-3
    permanent-bypass invariant regresses for any in-place upgrade
    rollout. ``clear_refine_in_flight_for_idx`` returns True for the
    legacy / no-refine_idx case."""
    set_id = compute_set_id([5])
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "baseline_iters": [5],
        "review": "orphan from a pre-round-4 runner",
    }))
    # Pre-round-4 JSON marker shape: pid + start_ts + set_id, no
    # refine_idx field. Use a fresh monotonic_ns so the round-4 TTL
    # check (which is also new) doesn't filter this marker — the
    # contract under test is the index-aware clear, not the TTL.
    import os as _os
    import time as _time
    marker = tmp_path / f".refine_inflight_{set_id}"
    marker.write_text(json.dumps({
        "pid": _os.getpid(),
        "start_ts": _time.monotonic_ns(),
        "set_id": set_id,
    }) + "\n", encoding="utf-8")
    (tmp_path / "status.json").write_text(json.dumps({"state": "shipped"}))

    recovered = chat_log.recover_orphan_refines(tmp_path)
    assert len(recovered) == 1
    assert active_refine_in_flight_set_ids(tmp_path) == set(), (
        "pre-round-4 JSON marker (no refine_idx field) must still be "
        "cleared after consumption — round-3 permanent-bypass "
        "invariant must survive in-place upgrade."
    )


def test_marker_payload_includes_refine_idx(tmp_path):
    """PR #25 round-4 finding #1: the marker payload MUST carry the
    bound ``refine_idx`` so recovery can refuse to clear when an
    older same-set orphan is consumed. Pin the on-disk shape so a
    regression that drops the field is caught immediately."""
    set_id = "deadbeef"
    mark_refine_in_flight(tmp_path, set_id, refine_idx=7)
    marker = tmp_path / f".refine_inflight_{set_id}"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["refine_idx"] == 7
    assert payload["set_id"] == set_id


def test_active_refine_set_ids_filters_pid_reuse_via_proc_starttime(tmp_path):
    """PR #25 round-4 finding #2: ``os.kill(pid, 0)`` only is
    insufficient — after a runner crashes and its pid is reused by
    ANY long-lived process (sshd reconnect, systemd unit restart,
    container respawn), the stale marker is treated as live forever.

    The fix cross-checks ``/proc/<pid>/stat`` field 22 (process start
    time, in clock ticks since boot) against the marker's stamped
    ``proc_starttime``. If they differ → pid was reused → treat as
    dead.

    We synthesise the reuse case by writing a marker for OUR OWN pid
    (so ``os.kill(pid, 0)`` succeeds) but with a ``proc_starttime``
    value that can never match this process's real start time.
    Linux-only; skipped on hosts without ``/proc/self/stat``."""
    import os as _os
    from pathlib import Path as _P

    if not _P("/proc/self/stat").exists():
        pytest.skip("PID-reuse cross-check is Linux-only (no /proc)")

    set_id = "deadbeef"
    marker = tmp_path / f".refine_inflight_{set_id}"
    # Use our own pid (alive on this host) but stamp a starttime
    # that's guaranteed NOT to match the live process's
    # /proc/self/stat field 22 (uptime is in jiffies — pick a value
    # at least 100 years away).
    marker.write_text(json.dumps({
        "pid": _os.getpid(),
        "start_ts": 0,
        "proc_starttime": -1,  # not a valid clock-ticks value
        "set_id": set_id,
        "refine_idx": 1,
    }) + "\n", encoding="utf-8")

    assert active_refine_in_flight_set_ids(tmp_path) == set(), (
        "PR #25 round-4 finding #2: a marker whose stamped "
        "proc_starttime differs from /proc/<pid>/stat field 22 "
        "MUST be filtered — the pid was reused by an unrelated "
        "process and the original runner is dead."
    )


def test_active_refine_set_ids_filters_marker_past_ttl(tmp_path):
    """PR #25 round-4 finding #2 (TTL fallback): even on hosts
    without /proc, a stale marker MUST be retired eventually so
    PID-reuse can't wedge the carve-out forever. The TTL bound is
    ``MAX_REFINE_DURATION_NS`` (30 minutes) — generous enough that
    a slow-but-completing turn never trips it.

    Synthesise a long-past start_ts and assert the marker is
    filtered. Use a stamped pid that's actually alive on this host
    (our own) so the os.kill check passes but the TTL still trips."""
    import os as _os

    set_id = "deadbeef"
    marker = tmp_path / f".refine_inflight_{set_id}"
    # start_ts that's MAX_REFINE_DURATION_NS + 1s in the past.
    from figcopy_runner.interface import MAX_REFINE_DURATION_NS as _TTL
    import time as _time
    stale_ts = _time.monotonic_ns() - _TTL - 1_000_000_000
    marker.write_text(json.dumps({
        "pid": _os.getpid(),
        "start_ts": stale_ts,
        # No proc_starttime → TTL is the only check beyond os.kill.
        "set_id": set_id,
        "refine_idx": 1,
    }) + "\n", encoding="utf-8")

    assert active_refine_in_flight_set_ids(tmp_path) == set(), (
        "PR #25 round-4 finding #2: a marker whose start_ts is "
        "older than MAX_REFINE_DURATION_NS MUST be retired so "
        "PID-reuse cannot wedge the carve-out forever on hosts "
        "without /proc."
    )


def test_active_refine_set_ids_filters_marker_from_future_monotonic(tmp_path):
    """Follow-up to PR #25 round-5 TTL finding: within a single boot,
    ``time.monotonic_ns()`` cannot move backwards. Any marker whose
    start_ts is in the future is stale/corrupt and should be retired
    immediately instead of waiting for the 30-minute TTL."""
    import os as _os
    import time as _time

    set_id = "deadbeef"
    marker = tmp_path / f".refine_inflight_{set_id}"
    marker.write_text(json.dumps({
        "pid": _os.getpid(),
        "start_ts": _time.monotonic_ns() + 1_000_000_000,
        "set_id": set_id,
        "refine_idx": 1,
    }) + "\n", encoding="utf-8")

    assert active_refine_in_flight_set_ids(tmp_path) == set()


def test_active_refine_set_ids_filters_malformed_json_marker(tmp_path):
    """PR #25 round-4 advisory (codex P2): a marker whose payload
    starts with '{' but is malformed JSON OR is JSON-shaped but
    missing/has-invalid pid is a BUG, not legacy compatibility.
    Treat as DEAD so a stray malformed marker can't wedge the
    carve-out forever. Legacy raw-string markers (no leading '{')
    keep their fail-open behaviour — that distinction is what makes
    in-place-upgrade safe."""
    set_id = "deadbeef"
    marker = tmp_path / f".refine_inflight_{set_id}"
    # Starts with '{' → claimed JSON. But malformed.
    marker.write_text("{not valid json", encoding="utf-8")
    assert active_refine_in_flight_set_ids(tmp_path) == set()

    # Valid JSON but missing pid.
    marker.write_text(json.dumps({"set_id": set_id}), encoding="utf-8")
    assert active_refine_in_flight_set_ids(tmp_path) == set()

    # Valid JSON but pid is non-int.
    marker.write_text(json.dumps({
        "pid": "not-an-int", "set_id": set_id,
    }), encoding="utf-8")
    assert active_refine_in_flight_set_ids(tmp_path) == set()


def test_reserve_refine_index_counts_existing_outputs_and_reservations(tmp_path):
    """Reservation files are part of the index space, so a turn that has
    claimed N but not written refine_NNN.json yet still protects N from
    reuse by a concurrent different-set refine."""
    (tmp_path / "refine_005.json").write_text("{}", encoding="utf-8")

    assert reserve_refine_index(tmp_path, "deadbeef") == 6
    assert next_refine_index(tmp_path) == 7
    assert reserve_refine_index(tmp_path, "cafebabe") == 7


def test_reserve_refine_index_is_atomic_across_set_ids(tmp_path):
    """PR #25 round-5 P1: different set_ids use different runner locks, so
    allocation itself must be workdir-global and atomic."""
    set_ids = [compute_set_id([i]) for i in range(1, 17)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        indices = list(pool.map(
            lambda sid: reserve_refine_index(tmp_path, sid),
            set_ids,
        ))

    assert sorted(indices) == list(range(1, len(set_ids) + 1))
    assert next_refine_index(tmp_path) == len(set_ids) + 1


def test_reserve_refine_index_rechecks_finalized_json_after_claim(
    tmp_path, monkeypatch,
):
    """PR #26 round-1 codex P1: a sibling runner can finalize
    ``refine_NNN.json`` and unlink ``.refine_reserved_NNN`` between
    ``next_refine_index``'s scan and the ``O_EXCL`` claim. Without the
    post-claim recheck, this loop would happily hand back the same N
    that's already used by a finalized output and the next turn would
    overwrite or misattribute it.

    Synthesise the race by pre-staging ``refine_002.json`` AFTER the
    inner ``next_refine_index`` returns 2 (achieved by stamping a
    placeholder reservation then deleting it from under the loop). We
    use a monkeypatch on ``next_refine_index`` to force the scan to
    return a stale value once."""
    import figcopy_runner.interface as iface

    # Pre-existing completed output at index 1 → vanilla next is 2.
    (tmp_path / "refine_001.json").write_text("{}", encoding="utf-8")

    # Force the inner scan to claim index 2 even though we will plant a
    # finalized refine_002.json under it before the O_EXCL succeeds.
    real_next = iface.next_refine_index
    seen = {"called": 0}

    def lying_next(workdir):
        seen["called"] += 1
        if seen["called"] == 1:
            # Plant a sibling-finalized JSON at the slot the scan is
            # about to pick. The reservation glob is empty, so the
            # vanilla scan would otherwise return 2 and the O_EXCL
            # would succeed against the unrelated reservation slot.
            (workdir / "refine_002.json").write_text("{}", encoding="utf-8")
            return 2
        return real_next(workdir)

    # PR #26 round-2 advisory: use pytest's ``monkeypatch.setattr``
    # fixture instead of raw assignment + manual try/finally. The
    # fixture restores the original on teardown even if the assertions
    # below raise mid-flight, so a failing test cannot leave the module
    # in a broken state for subsequent tests in the same worker.
    monkeypatch.setattr(iface, "next_refine_index", lying_next)
    idx = reserve_refine_index(tmp_path, "deadbeef")

    # The recheck after O_EXCL must have detected refine_002.json and
    # retried, so the returned idx must be > 2 (i.e. 3 — past both the
    # planted JSON and the prior ``refine_001.json``).
    assert idx == 3, (
        "PR #26 round-1 codex P1: after a sibling runner finalizes "
        "refine_NNN.json between scan and O_EXCL, reserve_refine_index "
        "MUST recheck and retry with a higher idx — not silently hand "
        "back the duplicated N."
    )
    # And the just-claimed reservation file at the duplicated slot must
    # have been released so subsequent allocations don't see it.
    assert not (tmp_path / ".refine_reserved_002").exists()


def test_reserve_refine_index_skips_dead_pid_reservation(tmp_path):
    """PR #26 round-1 P3 (code-reviewer-opus): reservation files leak
    when the owning runner is SIGKILL'd between
    ``reserve_refine_index`` and the runner's ``finally`` clear.
    Without filtering, ``next_refine_index`` would advance monotonically
    over the dead reservation forever.

    Synthesise a dead-pid reservation at index 5 alongside a finalized
    ``refine_001.json``. With the leak filter, the next allocation MUST
    pick index 2 (just past the finalized JSON; the dead reservation
    at 5 does NOT inflate the counter), and the dead reservation file
    MUST be GC'd. Without the filter, the next allocation would have
    inflated to index 6 and the stale file would persist forever."""
    import os as _os
    import subprocess as _sp

    proc = _sp.Popen(["true"])
    proc.wait()
    dead_pid = proc.pid
    try:
        _os.kill(dead_pid, 0)
    except ProcessLookupError:
        pass
    else:
        pytest.skip("pid was reused before the assertion; rerun")

    # Finalized output at slot 1 anchors the counter.
    (tmp_path / "refine_001.json").write_text("{}", encoding="utf-8")
    # Stale dead-pid reservation at slot 5 (would-be leak).
    res_path = tmp_path / ".refine_reserved_005"
    res_path.write_text(json.dumps({
        "pid": dead_pid,
        "start_ts": 0,
        "set_id": "deadbeef",
        "refine_idx": 5,
    }) + "\n", encoding="utf-8")

    # next_refine_index filters the dead reservation, so the counter is
    # bounded by the live finalized output (slot 1) → next is 2.
    assert next_refine_index(tmp_path) == 2, (
        "PR #26 round-1 P3: dead-pid reservation files MUST NOT inflate "
        "the global next_refine_index counter — without the filter, the "
        "stale slot 5 reservation would have pushed the counter to 6."
    )
    # And the GC pass must have unlinked the dead reservation.
    assert not res_path.exists(), (
        "Dead reservation file MUST be GC'd by next_refine_index so "
        "long-lived workdirs don't accumulate orphans forever."
    )

    # Subsequent allocation lands cleanly at slot 2, not 6.
    assert reserve_refine_index(tmp_path, "cafebabe") == 2


def test_clear_refine_reservation_unlinks_sidecar(tmp_path):
    """PR #26 round-1 advisory (pr-coherence): regression test for
    ``clear_refine_reservation`` so a future refactor can't silently
    drop the unlink. A leak would silently advance the global counter
    forever."""
    idx = reserve_refine_index(tmp_path, "deadbeef")
    res_path = tmp_path / f".refine_reserved_{idx:03d}"
    assert res_path.exists()
    clear_refine_reservation(tmp_path, idx)
    assert not res_path.exists()
    # Idempotent: clearing again is a no-op (no exception).
    clear_refine_reservation(tmp_path, idx)


def test_next_refine_index_skips_half_written_reservations(tmp_path):
    """PR #26 round-2 P3 (codex+opus 2-way agreement): a SIGKILL
    between ``os.open(...O_EXCL...)`` and the JSON ``fp.write(payload)``
    flush leaves a half-written reservation on disk. Round-1's
    ``_reservation_is_alive`` classified anything not parseable as JSON
    or missing a ``pid`` field as ALIVE (fail-open), which permanently
    leaks the slot — the same SIGKILL leak that round-1 fix #4 closed
    for fully-written reservations, just at a smaller window.

    Round-2 fix mirrors ``_marker_alive_now``: empty / non-JSON-shaped
    / JSON-shaped-but-malformed / missing-``pid`` reservations are now
    DEAD. This test pins each of those discrimination cases by writing
    the corruption shape directly and asserting ``next_refine_index``
    advances PAST the prior finalized output (slot 1) → 2, not past
    the planted half-written reservations.

    Without the fix, each of these calls would inflate ``next_refine_index``
    to ``max(planted_idx) + 1`` (i.e. 11, 21, 31, 41) and the half-written
    reservation file would persist forever.
    """
    # Fully-finalized output anchors the counter at 1.
    (tmp_path / "refine_001.json").write_text("{}", encoding="utf-8")

    # Each shape is a half-written / corrupt reservation a SIGKILL
    # between O_EXCL and fp.write(payload) could leave behind.
    shapes = {
        ".refine_reserved_010": "",                     # empty: opened, not flushed
        ".refine_reserved_020": "garbage not json",     # legacy-text-shaped: never existed
        ".refine_reserved_030": "{not valid json",      # JSON-shaped, malformed
        ".refine_reserved_040": json.dumps({"set_id": "abc"}),  # JSON dict, no pid
    }
    for name, body in shapes.items():
        (tmp_path / name).write_text(body, encoding="utf-8")

    # All four corrupt reservations MUST classify as DEAD; counter
    # bounded by the live finalized output (slot 1) → next is 2.
    assert next_refine_index(tmp_path) == 2, (
        "PR #26 round-2 P3: half-written / malformed / missing-pid "
        "reservation files MUST be classified DEAD so a SIGKILL "
        "between O_EXCL and the payload write doesn't permanently "
        "inflate the global next_refine_index counter. Without the "
        "fix, the planted slot 40 reservation would have pushed the "
        "counter to 41."
    )

    # And the dead reservations must have been GC'd by the scan, just
    # like the fully-written-but-dead-pid case in
    # ``test_reserve_refine_index_skips_dead_pid_reservation``. This
    # closes the long-lived-workdir orphan-accumulation hole.
    for name in shapes:
        assert not (tmp_path / name).exists(), (
            f"Dead/corrupt reservation file {name!r} MUST be GC'd by "
            f"next_refine_index."
        )


def test_next_refine_index_keeps_alive_reservation_with_live_pid(tmp_path):
    """Companion to ``test_next_refine_index_skips_half_written_reservations``:
    pin that the round-2 tightening did NOT regress the well-formed
    case. A reservation with a live pid + valid JSON payload MUST
    still be classified alive and inflate the counter.
    """
    import os as _os

    # Live owner: this very test process.
    own_pid = _os.getpid()

    res_path = tmp_path / ".refine_reserved_007"
    res_path.write_text(json.dumps({
        "pid": own_pid,
        "start_ts": 0,
        "set_id": "deadbeef",
        "refine_idx": 7,
    }) + "\n", encoding="utf-8")

    # Counter bounded by the live reservation slot 7 → next is 8.
    assert next_refine_index(tmp_path) == 8, (
        "Live-owner reservation MUST inflate the counter; the round-2 "
        "tightening of malformed handling must not regress this."
    )
    # And the live reservation must still exist on disk.
    assert res_path.exists()


def test_reserve_refine_index_no_empty_canonical_window_for_concurrent_allocators(
    tmp_path, monkeypatch,
):
    """PR #26 round-3 codex P1 (with reproducer): the round-2 fix to
    classify empty ``.refine_reserved_NNN`` reservations as DEAD let a
    concurrent scanner unlink the LIVE pre-flush canonical file of a
    healthy allocator (window between
    ``os.open(canonical, O_EXCL)`` and ``fp.write(payload)``). Two
    different ``set_id`` values both won the same ``refine_idx``,
    breaking the cross-set duplicate-allocation race that round-1
    finding #1 was supposed to close.

    Round-3 fix: stage the payload to
    ``.refine_reserved_NNN.tmp.<pid>.<ns>`` first, then ``os.link``
    into the canonical name. The canonical file is therefore never
    empty in healthy operation.

    This regression test reproduces codex's race against the round-3
    publication path. We wrap ``os.link`` so allocator A pauses
    INSIDE the publication step (canonical not yet visible because
    ``os.link`` has not landed); allocator B then runs its full
    reservation cycle (scan + claim), and we assert:

    1. Both allocators succeeded.
    2. They returned DISTINCT ``refine_idx`` values (no duplicate).
    3. After both complete, exactly one canonical file exists per
       returned idx, and no canonical file is empty.

    Under round-2 publication (``O_EXCL`` then ``fp.write``), the
    same shape of race let B's scan unlink A's canonical empty file,
    and B's claim succeeded against the just-freed slot → both
    returned the same idx. Under round-3, A's staging file is invisible
    to ``next_refine_index`` (its name doesn't parse as a reservation
    index), B's scan returns 1 (no canonical reservations), B
    publishes ``.refine_reserved_001``, A then resumes and ``os.link``
    fails with ``FileExistsError`` → A retries with idx=2.
    """
    import os as _os
    import threading
    import figcopy_runner.interface as iface

    real_link = _os.link

    pause_event = threading.Event()
    second_thread_done = threading.Event()
    paused_once = {"flag": False}

    def slow_link(src, dst, *args, **kwargs):
        # Pause the FIRST allocator that reaches the publication step
        # of its first attempt at the canonical idx=1 slot. Subsequent
        # allocators or retries proceed normally.
        if not paused_once["flag"] and str(dst).endswith(
            ".refine_reserved_001"
        ):
            paused_once["flag"] = True
            pause_event.set()
            # Wait for the second allocator to complete its full
            # reservation cycle before we publish under the canonical
            # name. This is the temporal cross-cut codex's repro
            # exposed: under round-2, the second allocator could
            # observe and unlink a live empty canonical file here.
            second_thread_done.wait(timeout=10.0)
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(_os, "link", slow_link)
    # Patch the binding inside the interface module too, since the
    # production code resolves ``os.link`` at call time (the patch on
    # the ``os`` module attribute is sufficient for that path; setting
    # it explicitly here is belt-and-braces against future imports).

    results: dict[str, int | str] = {}

    def alloc_a():
        try:
            results["a"] = iface.reserve_refine_index(tmp_path, "deadbeef")
        except Exception as e:  # pragma: no cover - test-time diagnostic
            results["a"] = f"ERROR: {type(e).__name__}: {e}"

    def alloc_b():
        # Wait until allocator A has actually entered its publication
        # step (we are paused inside ``slow_link``). Only then do B's
        # scan + claim, which is the moment when A's canonical state
        # could be observed under the round-2 publication path.
        pause_event.wait(timeout=10.0)
        try:
            results["b"] = iface.reserve_refine_index(tmp_path, "cafebabe")
        finally:
            second_thread_done.set()

    t_a = threading.Thread(target=alloc_a)
    t_b = threading.Thread(target=alloc_b)
    t_a.start()
    t_b.start()
    t_a.join(timeout=15.0)
    t_b.join(timeout=15.0)

    assert "a" in results and isinstance(results["a"], int), (
        f"allocator A failed: {results.get('a')!r}"
    )
    assert "b" in results and isinstance(results["b"], int), (
        f"allocator B failed: {results.get('b')!r}"
    )
    assert results["a"] != results["b"], (
        "PR #26 round-3 P1: two concurrent allocators on different "
        f"set_id values MUST return distinct refine_idx (got "
        f"a={results['a']!r}, b={results['b']!r}). The round-2 "
        "publication path allowed both to return the same idx because "
        "B's scan unlinked A's empty pre-flush canonical reservation."
    )

    # Both canonical reservation files must exist on disk after the
    # race; neither may be empty. (Empty would mean a publication step
    # surfaced a partial canonical file, which round-3 stage-then-link
    # eliminates.)
    canonicals = sorted(tmp_path.glob(".refine_reserved_*"))
    # Filter out any leftover .tmp.<pid>.<ns> staging files (best-effort
    # GC may have failed to unlink them; not a correctness bug, just
    # noise). The reservation parser ignores them for index counting.
    canonicals = [
        p for p in canonicals
        if "." not in p.name[len(".refine_reserved_"):]
    ]
    assert len(canonicals) == 2, (
        f"Expected 2 canonical reservations (one per allocator); got "
        f"{[p.name for p in canonicals]}"
    )
    for p in canonicals:
        text = p.read_text(encoding="utf-8")
        assert text.strip(), (
            f"PR #26 round-3 P1: canonical reservation {p.name!r} must "
            "never be empty under the stage-then-link publication path; "
            "an empty canonical file means a partial reservation was "
            "published and a concurrent scanner could classify it DEAD."
        )
        # Sanity: payload parses as JSON with a pid field, matching
        # the round-3 publication format.
        data = json.loads(text)
        assert "pid" in data and "refine_idx" in data and "set_id" in data, (
            f"reservation payload at {p.name!r} missing expected fields: "
            f"{data!r}"
        )


def test_reservation_is_alive_treats_non_utf8_payload_as_dead(tmp_path):
    """PR #26 round-3 advisory (opus): non-UTF-8 bytes under the
    canonical reservation name (disk corruption, buggy out-of-band
    writer) raise ``UnicodeDecodeError`` from ``read_text(encoding=
    "utf-8")``. ``UnicodeDecodeError`` is a ``ValueError`` subclass
    and was NOT caught by the bare ``except OSError`` arm — it would
    have escaped ``_reservation_is_alive`` and propagated up through
    ``next_refine_index`` → ``reserve_refine_index`` → killing the
    request with an unrelated exception class.

    Round-3 fix: catch ``UnicodeDecodeError`` and treat decode
    failures as DEAD, mirroring the JSON-malformed branch. Pin the
    behaviour with a binary payload that is not valid UTF-8 plus a
    pre-existing finalized output to anchor the counter.
    """
    from figcopy_runner.interface import _reservation_is_alive

    # Fully-finalized output anchors the counter at 1.
    (tmp_path / "refine_001.json").write_text("{}", encoding="utf-8")

    # Plant a reservation file whose contents are not valid UTF-8
    # (lone continuation byte 0x80; valid UTF-8 requires it follow a
    # multi-byte lead).
    res_path = tmp_path / ".refine_reserved_010"
    res_path.write_bytes(b"\x80\x81\x82 not utf-8 here")

    # Direct call: the classifier MUST return DEAD without raising.
    # Without the round-3 fix this raises UnicodeDecodeError.
    assert _reservation_is_alive(res_path) is False, (
        "PR #26 round-3 advisory: non-UTF-8 reservation payload MUST "
        "classify as DEAD, not propagate UnicodeDecodeError."
    )

    # End-to-end: next_refine_index sees the corrupt reservation,
    # treats it as DEAD, GCs it, and returns 2 (bounded by the live
    # finalized output). Without the fix, the UnicodeDecodeError
    # would propagate out of next_refine_index and reserve_refine_index
    # would die with the wrong exception class.
    assert next_refine_index(tmp_path) == 2, (
        "PR #26 round-3 advisory: non-UTF-8 reservation MUST not "
        "inflate the global counter (treated as DEAD)."
    )
    assert not res_path.exists(), (
        "Corrupt non-UTF-8 reservation must be GC'd by next_refine_index."
    )

    # And the allocator should land cleanly at 2 without observing the
    # decode error.
    assert reserve_refine_index(tmp_path, "deadbeef") == 2


def test_reserve_refine_index_bounded_attempts_raises_on_livelock(
    tmp_path, monkeypatch,
):
    """PR #26 round-2 advisory: defense-in-depth bound on the post-O_EXCL
    recheck loop. In practice the loop terminates because
    ``next_refine_index`` advances each iteration, but an unbounded
    ``while True:`` is the only one in the allocator path. Round-2
    converts it to ``for _ in range(MAX_RESERVE_ATTEMPTS)`` and raises
    ``OSError`` on exhaustion.

    Synthesise the livelock by monkey-patching ``next_refine_index`` to
    always return the same idx AND pre-staging the ``refine_NNN.json``
    that triggers the recheck. With the unbounded loop this would spin
    forever; with the bound it raises after MAX_RESERVE_ATTEMPTS
    iterations.
    """
    import figcopy_runner.interface as iface

    # Pre-stage the finalized JSON at a fixed idx so the post-claim
    # recheck always trips and the loop always retries.
    (tmp_path / "refine_007.json").write_text("{}", encoding="utf-8")

    def stuck_next(workdir):
        return 7

    monkeypatch.setattr(iface, "next_refine_index", stuck_next)
    # Tighten the cap for the test so it runs in milliseconds. Real
    # production code path uses MAX_RESERVE_ATTEMPTS == 1024.
    monkeypatch.setattr(iface, "MAX_RESERVE_ATTEMPTS", 4)

    with pytest.raises(OSError) as exc_info:
        reserve_refine_index(tmp_path, "deadbeef")
    assert "livelock" in str(exc_info.value).lower() or (
        "attempts" in str(exc_info.value).lower()
    ), (
        "PR #26 round-2 advisory: bounded reservation loop MUST raise "
        "a descriptive OSError on cap exhaustion, not silently spin or "
        "raise an opaque error."
    )


def test_clear_refine_in_flight_for_idx_concurrent_clearers_keep_atomicity(
    tmp_path,
):
    """PR #26 round-1 P2 (2/3 angles — code-reviewer-opus + pr-coherence):
    the new atomic-rename-claim path in ``clear_refine_in_flight_for_idx``
    (path.replace → re-read kind/idx → unlink-or-restore) had no
    regression test. The threat model: a runner stamps a marker once
    (under its own lock); recovery + the runner's own ``finally`` may
    then race to clear it. The contract: at most ONE clearer can win
    (return True), all others MUST return False (because the marker
    was claimed atomically before unlink, so the second claim raises
    FileNotFoundError and is treated as "already cleared by another
    consumer"); and the clearing rename intermediates MUST be reaped.

    We pin this with one stamp followed by 16 concurrent clearers all
    requesting the same idx. Without the atomic-rename claim, the
    ``path.unlink()`` race would let multiple clearers report success
    against the same marker (the underlying inode is the same; the
    ``not path.exists()`` check is a TOCTOU). With the claim, exactly
    one ``path.replace`` lands; the others raise FileNotFoundError →
    return False."""
    set_id = compute_set_id([7])
    mark_refine_in_flight(tmp_path, set_id, refine_idx=1)
    marker = tmp_path / f".refine_inflight_{set_id}"
    assert marker.exists()

    n_clearers = 16
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _i: clear_refine_in_flight_for_idx(tmp_path, set_id, 1),
            range(n_clearers),
        ))

    # Exactly one clearer must have won. Without the atomic-rename
    # claim, multiple clearers could TOCTOU-race past the existence
    # check and double-count.
    winners = sum(1 for r in results if r is True)
    assert winners == 1, (
        "PR #26 round-1 P2: with the atomic-rename claim path, exactly "
        f"one concurrent clearer must win (got {winners} wins out of "
        f"{n_clearers} clearers). Without the claim, the unlink TOCTOU "
        "would let multiple clearers double-report success."
    )
    # The marker must be gone (the winner unlinked the claimed sidecar).
    assert not marker.exists()
    # And no .clearing.<pid>.<ns> orphans must remain — every clearer
    # path either unlinks the claimed sidecar (winner) or never created
    # one (loser whose path.replace raised FileNotFoundError because
    # the winner already claimed).
    leftover = list(tmp_path.glob(f".refine_inflight_{set_id}.clearing.*"))
    assert leftover == [], (
        f"clearing sidecars must not leak after concurrent clearer race; "
        f"found {[p.name for p in leftover]}"
    )


def test_clear_refine_in_flight_for_idx_idx_mismatch_does_not_clobber_remark(
    tmp_path,
):
    """PR #26 round-1 P2 (post-rename JSON-with-mismatched-idx restore):
    the rollback path in ``clear_refine_in_flight_for_idx`` is reached
    when the post-claim re-read finds a JSON marker whose stamped idx
    does NOT match the requested idx. The marker MUST be restored to
    its pre-claim location.

    Stamp marker for refine_idx=1, then call clear with refine_idx=99
    (idx mismatch). The function MUST return False AND the marker must
    still exist after with refine_idx=1 intact."""
    set_id = compute_set_id([3])
    mark_refine_in_flight(tmp_path, set_id, refine_idx=1)
    marker = tmp_path / f".refine_inflight_{set_id}"
    cleared = clear_refine_in_flight_for_idx(tmp_path, set_id, 99)
    assert cleared is False
    assert marker.exists(), (
        "PR #26 round-1 P2: rollback restore path must put the marker "
        "back when post-claim idx doesn't match."
    )
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["refine_idx"] == 1
    # And no .clearing sidecar leaks from the rollback.
    leftover = list(tmp_path.glob(f".refine_inflight_{set_id}.clearing.*"))
    assert leftover == []


def test_clear_refine_in_flight_for_idx_rollback_does_not_clobber_concurrent_writer(
    tmp_path, monkeypatch,
):
    """PR #26 round-1 P3 (code-reviewer-opus): the rollback path in
    ``clear_refine_in_flight_for_idx`` previously used
    ``claimed.replace(path)`` which silently overwrites the destination
    on POSIX — a concurrent writer that stamped a fresh marker between
    our ``path.exists()`` check and our restore would have its marker
    clobbered. The fix uses ``os.link + claimed.unlink`` so the link
    fails with ``FileExistsError`` instead.

    Reproducing the race precisely is awkward because the rollback path
    only fires when the post-claim re-read disagrees with the pre-claim
    read (transient I/O / file vanishing under us). We monkeypatch the
    post-claim re-read to simulate a transient ``("missing", None)``,
    AND plant a sibling writer's fresh marker at the marker slot before
    the rollback runs. With ``claimed.replace(path)`` the writer's
    marker would be silently overwritten by our stale claimed sidecar;
    with ``os.link``, the link raises ``FileExistsError`` and the
    writer's marker survives intact.
    """
    import figcopy_runner.interface as iface

    set_id = compute_set_id([3])
    marker = tmp_path / f".refine_inflight_{set_id}"

    # Stamp marker for idx=1; clear with refine_idx=1 → pre-read
    # matches → enters the atomic claim. Force the post-claim re-read
    # to look like a transient miss so the rollback fires.
    mark_refine_in_flight(tmp_path, set_id, refine_idx=1)

    real_read = iface._read_marker_refine_idx
    state = {"reads": 0}

    def lying_read(path):
        state["reads"] += 1
        # First call is the pre-claim read on the original marker
        # path. Let it through so we enter the atomic claim.
        if state["reads"] == 1:
            return real_read(path)
        # Second call is the post-claim re-read on the claimed sidecar.
        # Plant a sibling writer's fresh marker at the marker slot
        # NOW, while the slot is empty (we already path.replace'd it
        # to claimed) — this is the concurrent writer the rollback
        # must not clobber.
        if ".clearing." in path.name:
            mark_refine_in_flight(tmp_path, set_id, refine_idx=99)
        return ("missing", None)

    # PR #26 round-2 advisory: pytest's ``monkeypatch.setattr`` fixture
    # restores the original on teardown automatically — guards against
    # leaving the module patched if any assertion below raises.
    monkeypatch.setattr(iface, "_read_marker_refine_idx", lying_read)
    cleared = clear_refine_in_flight_for_idx(tmp_path, set_id, 1)

    # We did NOT clear (post-read said missing → fall to rollback).
    assert cleared is False
    # The concurrent writer's fresh marker MUST have survived. If the
    # rollback had used ``claimed.replace(path)``, it would have
    # silently overwritten the writer's marker with the stale claimed
    # sidecar carrying refine_idx=1.
    assert marker.exists()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["refine_idx"] == 99, (
        "PR #26 round-1 P3: rollback path in clear_refine_in_flight_for_idx "
        "must not clobber a concurrent writer's fresh marker; using "
        "os.link instead of replace makes the link fail with FileExistsError "
        "rather than silently overwriting."
    )
    # The claimed sidecar must have been unlinked (cannot link to an
    # existing path → fall through to claimed.unlink in the rollback's
    # cleanup).
    leftover = list(tmp_path.glob(f".refine_inflight_{set_id}.clearing.*"))
    assert leftover == [], (
        f"clearing sidecars must not leak after rollback collision; "
        f"found {[p.name for p in leftover]}"
    )


def test_codex_runner_refine_propagates_reserve_oserror_not_unbound_local(
    tmp_path, monkeypatch,
):
    """PR #26 round-1 codex P2 + round-2 advisory: if
    ``reserve_refine_index`` raises BEFORE ``expected_n`` is assigned in
    ``CodexRunner.refine`` (e.g. ENOSPC / EACCES while creating
    ``.refine_reserved_NNN``), the ``finally`` cleanup previously
    evaluated an unbound local and masked the real I/O failure with
    ``UnboundLocalError``. ClaudeRunner did NOT have this issue.

    Round-1 pinned the fix with a structural source-text grep, but that
    test would still pass if a future refactor removed the surrounding
    ``try``/``finally`` entirely or shadowed the binding. The real
    contract is RUNTIME behavioral: a synthesised ``OSError`` from
    ``reserve_refine_index`` MUST propagate as ``OSError`` out of
    ``CodexRunner.refine``, NOT as ``UnboundLocalError``.

    We monkeypatch the module-level ``reserve_refine_index`` symbol the
    runner imported (``codex.reserve_refine_index``) to raise a tagged
    ``OSError``; ``pytest.raises`` then asserts the right class
    propagates. Smashing the ``expected_n: Optional[int] = None`` init
    in ``CodexRunner.refine`` would cause the ``finally`` block to hit
    ``UnboundLocalError`` instead and this test would fail.
    """
    from figcopy_runner.codex import CodexRunner
    from figcopy_runner import codex as codex_mod

    sentinel_msg = "ENOSPC: synthesised by test_codex_runner_refine_propagates_reserve_oserror_not_unbound_local"

    def boom(*args, **kwargs):
        raise OSError(sentinel_msg)

    # The runner closes over the module-level binding it imports as
    # ``reserve_refine_index`` from ``figcopy_runner.interface``;
    # patching that binding within ``codex.py`` is what the runtime
    # call site actually resolves against.
    monkeypatch.setattr(codex_mod, "reserve_refine_index", boom)

    runner = CodexRunner()
    with pytest.raises(OSError) as exc_info:
        runner.refine(tmp_path, baseline_iters=[1])
    assert sentinel_msg in str(exc_info.value), (
        "PR #26 round-1 codex P2: the original I/O failure class MUST "
        "propagate; an UnboundLocalError in the finally cleanup would "
        "mask the real cause and lose the sentinel message."
    )

    # And the per-(workdir, set_id) lock MUST have been released so a
    # follow-up refine call can run (release-on-failure invariant).
    # If the lock leaked, this second call would raise RefineInFlight.
    with pytest.raises(OSError):
        runner.refine(tmp_path, baseline_iters=[1])


def test_reserve_refine_index_fsyncs_staging_before_publish(tmp_path, monkeypatch):
    """PR #26 round-4 P3 (opus, durability): the round-3 stage-then-link
    publication path goes ``fdopen.write(payload) → fp.close → os.link
    (staging → canonical)``. Without an ``fsync`` between the user-space
    flush at ``fp.close`` and the inode-publishing ``os.link``, a power
    loss can crash with the canonical hardlink already published but
    its underlying inode contents not yet flushed to stable storage.
    Post-recovery, that is the same "empty canonical residue" shape
    round-2 ``_reservation_is_alive`` classifies as DEAD — the slot is
    still recoverable via the GC pass, but the publication path SHOULD
    only expose durable bytes.

    We pin the contract by intercepting ``os.fsync`` and asserting it
    is called on the staging file's fd BEFORE ``os.link`` runs. We
    capture the (fd → path) mapping by also intercepting ``os.open``
    so we know which fd corresponds to the staging path; then in
    ``slow_link`` we assert the staging-fd was fsynced prior to the
    link call. Without the round-4 fix, no fsync occurs and the
    assertion fails inside ``slow_link``.
    """
    import os as _os
    import figcopy_runner.interface as iface

    real_open = _os.open
    real_fsync = _os.fsync
    real_link = _os.link

    fd_to_path: dict[int, str] = {}
    fsynced_fds: set[int] = set()
    fsynced_staging_paths: set[str] = set()

    def tracking_open(path, flags, mode=0o777, *args, **kwargs):
        fd = real_open(path, flags, mode, *args, **kwargs)
        # Only track our reservation-staging opens; everything else
        # (atomic_write_text intermediates etc.) stays untracked so we
        # don't conflate fsyncs from siblings.
        name = _os.fspath(path)
        if ".refine_reserved_" in name and ".tmp." in name:
            fd_to_path[fd] = name
        return fd

    def tracking_fsync(fd):
        fsynced_fds.add(fd)
        if fd in fd_to_path:
            fsynced_staging_paths.add(fd_to_path[fd])
        return real_fsync(fd)

    def asserting_link(src, dst, *args, **kwargs):
        src_str = _os.fspath(src)
        dst_str = _os.fspath(dst)
        # The src must be a staging file we tracked, and it MUST have
        # been fsynced prior to the link call.
        assert ".refine_reserved_" in src_str and ".tmp." in src_str, (
            f"reserve_refine_index publication must link FROM a staging "
            f"path, got src={src_str!r}"
        )
        assert src_str in fsynced_staging_paths, (
            "PR #26 round-4 P3 (opus durability): the staging file "
            f"{src_str!r} MUST be fsynced BEFORE os.link publishes it "
            "into the canonical reservation name. Without fsync, a "
            "power loss between fp.close and os.link can leave an empty "
            "canonical inode on disk."
        )
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(_os, "open", tracking_open)
    monkeypatch.setattr(_os, "fsync", tracking_fsync)
    monkeypatch.setattr(_os, "link", asserting_link)

    idx = iface.reserve_refine_index(tmp_path, "deadbeef")
    assert idx == 1
    # Sanity: at least one staging fd was fsynced (the one for this
    # successful allocation; FileExistsError-retry paths can produce
    # additional fsync-then-discard rounds).
    assert fsynced_staging_paths, (
        "PR #26 round-4 P3: no staging path was fsynced during a "
        "successful reservation — the fsync was elided entirely."
    )


def test_next_refine_index_gcs_dead_pid_staging_orphans(tmp_path):
    """PR #26 round-4 P3 (pr-coherence, gc-asymmetry): the round-3
    publication path stages payloads to ``.refine_reserved_NNN.tmp.<pid>.<ns>``
    before ``os.link``-ing into the canonical name. Three failure shapes
    can leak the staging file: SIGKILL between O_EXCL and the cleanup
    arm, a cross-FS ``EXDEV`` whose own ``staging.unlink()`` cleanup
    arm fails, or a silent post-publish unlink failure. The reservation
    parser already filters these orphans out for index counting (their
    suffix doesn't parse as ``int``), so this is purely a workdir
    cleanliness sweep — symmetric to ``_gc_clearing_sidecars`` for the
    in-flight marker side.

    Round-4 fix: ``_gc_reservation_staging_orphans`` is called from
    ``next_refine_index``; staging files whose stamped pid is no longer
    alive are unlinked best-effort. Living-pid staging files are left
    alone — their owning ``reserve_refine_index`` call may still be
    mid-flight.

    Pin both branches: a dead-pid orphan MUST be GC'd, a live-pid
    orphan (we use this process's own pid) MUST be left alone.
    """
    import os as _os
    import time as _time

    # Anchor the counter at 1 with a finalized output so we can assert
    # next_refine_index returns 2 (orphans must not inflate it either).
    (tmp_path / "refine_001.json").write_text("{}", encoding="utf-8")

    # Plant a dead-pid staging orphan. Pick a pid we can reasonably
    # assume is dead: pid 2**31 - 1 (above PID_MAX_LIMIT on Linux,
    # ``os.kill(pid, 0)`` raises ProcessLookupError).
    dead_pid = 2**31 - 1
    dead_orphan = tmp_path / (
        f".refine_reserved_010.tmp.{dead_pid}.{_time.monotonic_ns()}"
    )
    dead_orphan.write_text("stale", encoding="utf-8")

    # Plant a live-pid staging orphan (this process). It MUST survive
    # the sweep because its owning allocator might still be mid-flight.
    live_orphan = tmp_path / (
        f".refine_reserved_020.tmp.{_os.getpid()}.{_time.monotonic_ns()}"
    )
    live_orphan.write_text("in-flight", encoding="utf-8")

    # Plant a malformed-pid staging file — the GC must silently skip
    # it (treat as opaque, leave alone) rather than crash on int().
    bogus = tmp_path / ".refine_reserved_030.tmp.notapid.0"
    bogus.write_text("bogus", encoding="utf-8")

    # Trigger the sweep via next_refine_index; staging files DO NOT
    # parse as reservation indices so they should not inflate the
    # counter regardless of the GC outcome.
    nxt = next_refine_index(tmp_path)
    assert nxt == 2, (
        "PR #26 round-4 P3: staging .tmp.<pid>.<ns> orphans must NEVER "
        "inflate next_refine_index (their name doesn't parse as int); "
        f"got {nxt}."
    )

    # Dead-pid orphan must have been GC'd.
    assert not dead_orphan.exists(), (
        "PR #26 round-4 P3: dead-pid staging orphan was not GC'd by "
        "next_refine_index. Symmetric to _gc_clearing_sidecars for "
        "the in-flight marker side; without it, long-lived workdirs "
        "accumulate staging files indefinitely after process crashes."
    )

    # Live-pid orphan must NOT have been touched (allocator may still
    # be mid-flight).
    assert live_orphan.exists(), (
        "PR #26 round-4 P3: live-pid staging file was incorrectly "
        "GC'd. Living-pid staging files MUST be left alone — their "
        "owning reserve_refine_index call may still be mid-flight "
        "and racing the GC could break a healthy allocation."
    )

    # Malformed-pid staging file must be left alone (GC silently
    # ignores unparseable pids).
    assert bogus.exists(), (
        "PR #26 round-4 P3: GC must silently skip staging files with "
        "unparseable pid suffix, not unlink or crash."
    )


def test_reserve_refine_index_propagates_exdev_and_cleans_staging(
    tmp_path, monkeypatch,
):
    """PR #26 round-4 P3 (pr-coherence, missing-test): the round-3
    publication path's ``except OSError`` arm at the ``os.link`` call
    site (interface.py:1102-1113) covers cross-FS ``EXDEV`` and other
    hardlink-unsupported workdirs (FAT/exFAT, some FUSE mounts). The
    contract documented in the PR body's deploy-notes section is:
    ``os.link`` raises ``OSError``, the per-(workdir, set_id) lock
    releases in the caller's ``finally``, and the SSE error path
    surfaces the original I/O class.

    No regression test exercised this branch. Round-4 pins it: a
    monkey-patched ``os.link`` that always raises ``OSError(EXDEV)``
    MUST cause ``reserve_refine_index`` to (a) propagate the SAME
    OSError class (no UnboundLocalError, no FileExistsError, no
    swallowed-and-retried), and (b) clean up the staging file it just
    wrote. Item (b) is the cleanup-arm correctness check — if a
    workdir is on an EXDEV-failing FS, we don't want every retry to
    leave a fresh staging orphan.
    """
    import os as _os
    import errno as _errno
    import figcopy_runner.interface as iface

    def exdev_link(src, dst, *args, **kwargs):
        raise OSError(
            _errno.EXDEV,
            "Invalid cross-device link (synthesised by "
            "test_reserve_refine_index_propagates_exdev_and_cleans_staging)",
            str(dst),
        )

    monkeypatch.setattr(_os, "link", exdev_link)

    with pytest.raises(OSError) as exc_info:
        iface.reserve_refine_index(tmp_path, "deadbeef")
    # The error class must be OSError with the EXDEV errno preserved
    # (NOT swallowed and retried, NOT FileExistsError, NOT
    # UnboundLocalError).
    assert exc_info.value.errno == _errno.EXDEV, (
        "PR #26 round-4 P3 (pr-coherence): the os.link cross-FS arm "
        "MUST propagate the original OSError class with errno EXDEV "
        f"preserved; got errno={exc_info.value.errno!r}."
    )

    # The staging file MUST have been cleaned up by the cleanup arm.
    # Without that cleanup, every EXDEV-failing retry would leak a
    # fresh staging file forever (and the GC sweep would only collect
    # them once the publishing process died, which is not the
    # required-cleanliness contract for healthy long-running runners
    # on a misconfigured workdir).
    leftover_staging = list(
        tmp_path.glob(".refine_reserved_*.tmp.*")
    )
    assert leftover_staging == [], (
        "PR #26 round-4 P3 (pr-coherence): the os.link cross-FS arm's "
        "except-OSError block MUST unlink the staging file before "
        "re-raising. Leaked staging files: "
        f"{[p.name for p in leftover_staging]}."
    )

    # And no canonical reservation may exist either — the failure
    # happens BEFORE publication.
    canonicals = [
        p for p in tmp_path.glob(".refine_reserved_*")
        if "." not in p.name[len(".refine_reserved_"):]
    ]
    assert canonicals == [], (
        "PR #26 round-4 P3 (pr-coherence): a failed os.link MUST NOT "
        "leave a canonical reservation behind; got "
        f"{[p.name for p in canonicals]}."
    )

