"""Regression tests for the Stage-0 scaffolding in CodexRunner.

Covers the (a)-bucket fixes from the commit-quality-pipe Mode B audit
of 5bbf58a:

- `is_data_placeholder` exact-match contract (P1: substring was too
  permissive; an agent-generated file containing the marker as a
  comment was misclassified as still-placeholder)
- `DATA_PLACEHOLDER_TEXT` round-trip detection
- `start()` concurrent-call gate (P0: cancel+restart race could let
  a stale orchestrator thread clobber the new run's state)
- `_own_generation` ownership check used by `_stage1_orchestrate` to
  bail when a later start() superseded it

The subprocesses themselves are integration-tested via live runs; these
tests cover only the orchestration logic that runs in-process.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from figcopy_runner.codex import CodexRunner
from figcopy_runner.event_bus import EventBus
from figcopy_runner.interface import (
    DATA_PLACEHOLDER_TEXT,
    is_data_placeholder,
)
from figcopy_runner import event_bus


# ───── is_data_placeholder + DATA_PLACEHOLDER_TEXT ────────────────────


def test_placeholder_round_trip():
    """The constant the server writes is detected by the runner."""
    assert is_data_placeholder(DATA_PLACEHOLDER_TEXT)


def test_placeholder_match_is_trimmed():
    """Trailing whitespace doesn't break detection — the server might
    add a newline, an editor might strip one, and either is fine."""
    assert is_data_placeholder(DATA_PLACEHOLDER_TEXT + "\n\n")
    assert is_data_placeholder(DATA_PLACEHOLDER_TEXT.rstrip())


def test_placeholder_none_is_not_placeholder():
    assert not is_data_placeholder(None)


def test_placeholder_empty_is_not_placeholder():
    assert not is_data_placeholder("")
    assert not is_data_placeholder("   \n  ")


def test_agent_output_with_marker_in_comment_not_misclassified():
    """An agent-generated CSV that happens to contain the marker as a
    line comment must NOT be classified as still-placeholder. This is
    the regression the substring match would have allowed (a P1 from
    Phase-1 review)."""
    agent_output = (
        "# (Note: the input said 'No data provided', synthesized below)\n"
        "x,y\n"
        "0,1.2\n"
        "1,3.4\n"
        "2,5.6\n"
    )
    assert not is_data_placeholder(agent_output)
    # And specifically, the marker substring IS present —
    # the contrast with the old substring check is the whole point.
    assert "No data provided" in agent_output


def test_legit_short_data_not_rejected():
    """A small but valid data file (4 categories) is shorter than the
    old `< 20` magic threshold but is legitimate output. Detection
    must allow it through (the runner reads it via the placeholder
    check + emptiness check; both pass here)."""
    short_csv = "a,1\nb,2\nc,3\nd,4\n"
    assert not is_data_placeholder(short_csv)
    assert short_csv.strip()  # the runner's non-empty check


# ───── start() concurrent-call gate ───────────────────────────────────


def test_start_rejects_concurrent_call_for_same_workdir(tmp_path,
                                                         monkeypatch):
    """A second start() on the same workdir while the first is still
    running must return without spawning a second orchestrator thread.

    Without this gate, two background threads race on inputs/data.txt
    + the lifecycle registry. P0 from Phase-1 review.
    """
    runner = CodexRunner()
    # Stub the background driver so it doesn't actually try to spawn
    # codex. We just need start() to flip the state to "running" and
    # leave it there.
    started = threading.Event()
    released = threading.Event()
    def _fake_driver(workdir, prompt, max_iters, auto, gen):
        started.set()
        # Block until the test releases us — simulates a long-running
        # orchestrator thread.
        released.wait(timeout=2.0)
    monkeypatch.setattr(
        runner, "_stage1_orchestrate", _fake_driver,
    )

    wd = tmp_path / "run1"
    # First start: spawns the driver thread.
    name = runner.start(wd)
    assert name == "run1"
    assert started.wait(timeout=1.0), "first driver thread didn't start"

    # State should now be "running" with gen=1.
    with runner._iter_state_lock:
        s1 = runner._iter_state[wd.resolve()].copy()
    assert s1["state"] == "running"
    assert s1["gen"] == 1

    # Second start while the first is still in flight: must be a
    # no-op (returns the same name, doesn't bump generation, doesn't
    # spawn a second driver).
    name2 = runner.start(wd)
    assert name2 == "run1"
    with runner._iter_state_lock:
        s2 = runner._iter_state[wd.resolve()].copy()
    assert s2["gen"] == 1, "concurrent start() must not bump generation"

    # Cleanup.
    released.set()


def test_start_after_terminal_state_bumps_generation(tmp_path,
                                                     monkeypatch):
    """Once the prior run has reached a terminal state (shipped /
    failed / cancelled), start() should accept the next call and
    increment the generation counter."""
    runner = CodexRunner()
    monkeypatch.setattr(
        runner, "_stage1_orchestrate", lambda *a, **kw: None,
    )

    wd = tmp_path / "run-restart"
    runner.start(wd)
    # Simulate terminal state from the prior run.
    with runner._iter_state_lock:
        runner._iter_state[wd.resolve()]["state"] = "shipped"

    runner.start(wd)
    with runner._iter_state_lock:
        s = runner._iter_state[wd.resolve()].copy()
    assert s["state"] == "running"
    assert s["gen"] == 2, "post-terminal restart should bump gen"


def test_start_after_cancel_bumps_generation(tmp_path, monkeypatch):
    """Cancel is a terminal state; the next start() should be allowed
    and should mint a fresh generation."""
    runner = CodexRunner()
    monkeypatch.setattr(
        runner, "_stage1_orchestrate", lambda *a, **kw: None,
    )

    wd = tmp_path / "run-cancel"
    runner.start(wd)
    with runner._iter_state_lock:
        runner._iter_state[wd.resolve()]["state"] = "cancelled"

    runner.start(wd)
    with runner._iter_state_lock:
        s = runner._iter_state[wd.resolve()].copy()
    assert s["state"] == "running"
    assert s["gen"] == 2


# ───── _own_generation ────────────────────────────────────────────────


def test_own_generation_true_when_state_matches(tmp_path):
    runner = CodexRunner()
    wd = (tmp_path / "wd").resolve()
    with runner._iter_state_lock:
        runner._iter_state[wd] = {
            "state": "running", "current_iter": None, "gen": 7,
        }
    assert runner._own_generation(wd, 7)


def test_own_generation_false_when_superseded(tmp_path):
    """A stale orchestrator thread should see False once a later
    start() has bumped the generation. The thread uses this to bail
    without mutating state (P0 from Phase-1 review)."""
    runner = CodexRunner()
    wd = (tmp_path / "wd2").resolve()
    with runner._iter_state_lock:
        runner._iter_state[wd] = {
            "state": "running", "current_iter": None, "gen": 5,
        }
    # Stale thread captured gen=4 before the later start() bumped to 5.
    assert not runner._own_generation(wd, 4)


def test_own_generation_false_when_state_missing(tmp_path):
    """If `_iter_state[workdir]` has been removed entirely (cleanup,
    test isolation, etc.), ownership check returns False so callers
    treat their own writes as deferred."""
    runner = CodexRunner()
    wd = (tmp_path / "wd3").resolve()
    assert not runner._own_generation(wd, 1)


# ───── _stage1_orchestrate TurnEnd early returns ─────────────────────


def _placeholder_workdir(tmp_path: Path, name: str = "run") -> Path:
    wd = (tmp_path / name).resolve()
    inputs = wd / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "data.txt").write_text(DATA_PLACEHOLDER_TEXT, encoding="utf-8")
    (inputs / "reference_clean.png").write_bytes(b"fake-png")
    return wd


def _iter_event_types(bus: EventBus, wd: Path) -> list[tuple[str, dict]]:
    return [
        (e["type"], e.get("data", {}))
        for e in bus.replay(wd, "iter", since_seq=0)
    ]


def test_stage1_datagen_cancel_emits_turn_end(tmp_path, monkeypatch):
    """If cancel() lands while the data-gen pass is running, the
    TurnStartEvent emitted by _stage1_orchestrate must still be paired
    with a terminal TurnEndEvent so SSE consumers clear their spinner."""
    runner = CodexRunner()
    bus = EventBus()
    monkeypatch.setattr(event_bus, "get_bus", lambda: bus)
    monkeypatch.setattr(runner, "_spawn_orchestrator", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "_run_reference_preprocess_pass", lambda _wd: True)
    wd = _placeholder_workdir(tmp_path, "cancel")
    with runner._iter_state_lock:
        runner._iter_state[wd] = {
            "state": "running",
            "current_iter": None,
            "gen": 1,
        }

    def fake_datagen(_wd):
        with runner._iter_state_lock:
            runner._iter_state[wd]["state"] = "cancelled"
        return False

    monkeypatch.setattr(runner, "_run_data_gen_pass", fake_datagen)
    runner._stage1_orchestrate(wd, "", 6, False, 1)

    events_seen = _iter_event_types(bus, wd)
    assert events_seen[0][0] == "turn_start"
    assert events_seen[-1] == (
        "turn_end",
        {"status": "cancelled", "reason": "cancelled"},
    )


def test_stage1_datagen_superseded_emits_turn_end(tmp_path, monkeypatch):
    """If a newer start() takes over the workdir during data-gen, the
    stale orchestrator must close its own TurnStartEvent before bailing."""
    runner = CodexRunner()
    bus = EventBus()
    monkeypatch.setattr(event_bus, "get_bus", lambda: bus)
    monkeypatch.setattr(runner, "_spawn_orchestrator", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "_run_reference_preprocess_pass", lambda _wd: True)
    wd = _placeholder_workdir(tmp_path, "superseded")
    with runner._iter_state_lock:
        runner._iter_state[wd] = {
            "state": "running",
            "current_iter": None,
            "gen": 1,
        }

    def fake_datagen(_wd):
        with runner._iter_state_lock:
            runner._iter_state[wd]["gen"] = 2
        return True

    monkeypatch.setattr(runner, "_run_data_gen_pass", fake_datagen)
    runner._stage1_orchestrate(wd, "", 6, False, 1)

    events_seen = _iter_event_types(bus, wd)
    assert events_seen[0][0] == "turn_start"
    assert events_seen[-1] == (
        "turn_end",
        {"status": "cancelled", "reason": "superseded by newer run"},
    )


def test_stage1_final_generation_guard_emits_turn_end(tmp_path, monkeypatch):
    """The final pre-spawn ownership guard is another supersede exit
    path; it also needs to close the lifecycle envelope."""
    runner = CodexRunner()
    bus = EventBus()
    monkeypatch.setattr(event_bus, "get_bus", lambda: bus)
    spawned = False

    def fake_spawn(*_args, **_kwargs):
        nonlocal spawned
        spawned = True

    monkeypatch.setattr(runner, "_spawn_orchestrator", fake_spawn)
    monkeypatch.setattr(runner, "_run_reference_preprocess_pass", lambda _wd: True)
    monkeypatch.setattr(runner, "_run_data_gen_pass", lambda _wd: True)
    wd = _placeholder_workdir(tmp_path, "final-guard")
    with runner._iter_state_lock:
        runner._iter_state[wd] = {
            "state": "running",
            "current_iter": None,
            "gen": 1,
        }

    monkeypatch.setattr(runner, "_own_generation", lambda _wd, _gen: False)
    runner._stage1_orchestrate(wd, "", 6, False, 1)

    assert not spawned
    events_seen = _iter_event_types(bus, wd)
    assert events_seen[0][0] == "turn_start"
    assert events_seen[-1] == (
        "turn_end",
        {"status": "cancelled", "reason": "superseded by newer run"},
    )


def test_stage1_reference_preprocess_failure_emits_turn_end(tmp_path, monkeypatch):
    """Reference preprocessing is now the first Stage-0 gate. If it
    fails, the run must close its SSE lifecycle and persist a failed
    status without launching data-gen or the orchestrator."""
    runner = CodexRunner()
    bus = EventBus()
    monkeypatch.setattr(event_bus, "get_bus", lambda: bus)
    wd = _placeholder_workdir(tmp_path, "preprocess-fail")
    with runner._iter_state_lock:
        runner._iter_state[wd] = {
            "state": "running",
            "current_iter": None,
            "gen": 1,
        }

    monkeypatch.setattr(runner, "_run_reference_preprocess_pass", lambda _wd: False)
    monkeypatch.setattr(
        runner, "_run_data_gen_pass",
        lambda _wd: pytest.fail("data-gen should not run after preprocess failure"),
    )
    monkeypatch.setattr(
        runner, "_spawn_orchestrator",
        lambda *_args, **_kwargs: pytest.fail("orchestrator should not spawn"),
    )

    runner._stage1_orchestrate(wd, "", 6, False, 1)

    events_seen = _iter_event_types(bus, wd)
    assert events_seen[0][0] == "turn_start"
    assert events_seen[-1] == (
        "turn_end",
        {"status": "failed", "reason": "reference preprocessing pass failed"},
    )
