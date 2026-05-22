"""claude.py pure-helper + refine-flow tests.

Mirrors ``test_codex_helpers.py``: locks in the most fragile parts of
ClaudeRunner — the stream-json → SessionEvent translator
(``_publish_claude_event``) and the multi-turn ``_refine_locked``
machinery (subprocess stubbed out).

The live ``claude --print`` subprocess + reader-thread paths are
covered by an end-to-end smoke; here we just pin the contracts that
PR #19's review surfaced as broken at the time of writing:

- regression for `to_prose(adjustments)` missing its required
  ``message`` arg (#1) — ``test_refine_locked_passes_both_args_to_to_prose``
- regression for ``ev_complete.get("seq")`` AttributeError on
  RefineCompleteEvent dataclass (#2) — covered by
  ``test_refine_locked_writes_chat_with_event_bus_seq``
- regression for refine-timeout missing TurnEndEvent (#3) —
  ``test_refine_locked_publishes_turn_end_on_timeout``
- regression for absolute /static/ image_url violating the runner
  contract (#4) — ``test_refine_locked_returns_relative_image_url``
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import pytest

from figcopy_runner import claude as claude_mod
from figcopy_runner import events as ev
from figcopy_runner.claude import (
    ClaudeRunner,
    RefineFailed,
    _next_refine_index,
    _parse_iter_n_from_path,
    _parse_jsonl_line,
    _publish_claude_event,
    _summarize_tool_input,
    _truncate,
)
from figcopy_runner.event_bus import EventBus
from figcopy_runner.interface import compute_set_id


# ─────────────────────────── pure helpers ─────────────────────────────


def test_parse_iter_n_basic():
    assert _parse_iter_n_from_path("img_iter3.png") == 3
    assert _parse_iter_n_from_path("img_iter12.png") == 12


def test_parse_iter_n_misses_non_iter_files():
    # claude.py's variant uses a startswith/endswith check rather than
    # a regex, so exercise its specific narrow shape.
    assert _parse_iter_n_from_path("figure_iter3.py") is None
    assert _parse_iter_n_from_path("img.png") is None
    assert _parse_iter_n_from_path("refine_001.png") is None
    assert _parse_iter_n_from_path("img_iter3.jpg") is None
    # Non-numeric trailer gets handled via the ValueError branch.
    assert _parse_iter_n_from_path("img_iterabc.png") is None


def test_parse_jsonl_line_handles_garbage():
    assert _parse_jsonl_line(b"") is None
    assert _parse_jsonl_line(b"\n") is None
    assert _parse_jsonl_line(b"not json") is None
    assert _parse_jsonl_line(b'"just-a-string"') is None
    assert _parse_jsonl_line(b'{"type": "x"}')["type"] == "x"


def test_next_refine_index_empty(tmp_path):
    assert _next_refine_index(tmp_path) == 1


def test_next_refine_index_existing(tmp_path):
    (tmp_path / "refine_001.json").write_text("{}")
    (tmp_path / "refine_002.json").write_text("{}")
    (tmp_path / "refine_005.json").write_text("{}")
    # max(1, 2, 5) + 1 = 6
    assert _next_refine_index(tmp_path) == 6


def test_summarize_tool_input_command():
    assert _summarize_tool_input({"command": "ls -la"}) == "ls -la"


def test_summarize_tool_input_file_path():
    out = _summarize_tool_input({"file_path": "/tmp/x.py"})
    assert "file_path=" in out and "/tmp/x.py" in out


def test_summarize_tool_input_path_alias():
    # The Read tool uses `path` instead of `file_path` for some
    # variants; we route both through the same summary shape.
    assert _summarize_tool_input({"path": "/etc/hosts"}) == "path=/etc/hosts"


def test_summarize_tool_input_description_fallback():
    out = _summarize_tool_input({"description": "Run unit tests"})
    assert "Run unit tests" in out


def test_summarize_tool_input_unknown_keys_jsondumps():
    # Anything else round-trips through json.dumps for compactness.
    out = _summarize_tool_input({"weirdkey": [1, 2, 3]})
    assert "weirdkey" in out


def test_truncate_keeps_short_strings():
    assert _truncate("hi", 10) == "hi"


def test_truncate_chops_with_ellipsis():
    out = _truncate("a" * 50, 10)
    assert len(out) == 10
    # Last char is the unicode ellipsis.
    assert out.endswith("…")


# ─────────────────── _publish_claude_event branches ───────────────────


def test_publish_system_init_no_event_emitted(tmp_path):
    bus = EventBus()
    _publish_claude_event(bus, tmp_path, "iter", {
        "type": "system", "subtype": "init",
        "session_id": "abc-uuid",
    })
    types = [e["type"] for e in bus.replay(tmp_path, "iter", since_seq=0)]
    # The translator deliberately swallows system:init — the runner
    # captures session_id directly from the dict.
    assert types == []


def test_publish_assistant_text_emits_text(tmp_path):
    bus = EventBus()
    _publish_claude_event(bus, tmp_path, "refine:s", {
        "type": "assistant",
        "message": {"content": [
            {"type": "text", "text": "hello world"},
        ]},
    })
    events_seen = list(bus.replay(tmp_path, "refine:s", since_seq=0))
    assert [e["type"] for e in events_seen] == ["text"]
    assert events_seen[0]["data"]["text"] == "hello world"
    assert events_seen[0]["data"]["is_partial"] is False


def test_publish_assistant_blank_text_skipped(tmp_path):
    bus = EventBus()
    _publish_claude_event(bus, tmp_path, "iter", {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "   "}]},
    })
    types = [e["type"] for e in bus.replay(tmp_path, "iter", since_seq=0)]
    assert types == []


def test_publish_assistant_tool_use_emits_tool_call_start(tmp_path):
    bus = EventBus()
    _publish_claude_event(bus, tmp_path, "iter", {
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash",
             "input": {"command": "python figure_iter1.py"}},
        ]},
    })
    events_seen = list(bus.replay(tmp_path, "iter", since_seq=0))
    assert [e["type"] for e in events_seen] == ["tool_call_start"]
    d = events_seen[0]["data"]
    assert d["call_id"] == "toolu_1"
    assert d["name"] == "Bash"
    # `args` is normalized to a compact summary string (not the raw
    # input dict). The compactor surfaces the command verbatim.
    assert "python figure_iter1.py" in d["args"]


def test_publish_assistant_thinking_block_skipped(tmp_path):
    bus = EventBus()
    _publish_claude_event(bus, tmp_path, "iter", {
        "type": "assistant",
        "message": {"content": [
            {"type": "thinking", "thinking": "internal monologue"},
        ]},
    })
    types = [e["type"] for e in bus.replay(tmp_path, "iter", since_seq=0)]
    # We deliberately do NOT surface model reasoning to the SSE stream.
    assert types == []


def test_publish_user_tool_result_string_content_emits_tool_call_end(tmp_path):
    bus = EventBus()
    _publish_claude_event(bus, tmp_path, "iter", {
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1",
             "content": "exit 0", "is_error": False},
        ]},
    })
    events_seen = list(bus.replay(tmp_path, "iter", since_seq=0))
    assert [e["type"] for e in events_seen] == ["tool_call_end"]
    d = events_seen[0]["data"]
    assert d["call_id"] == "toolu_1"
    assert d["ok"] is True
    assert d["result"] == "exit 0"
    assert d["error"] is None


def test_publish_user_tool_result_list_content_concatenates_text(tmp_path):
    bus = EventBus()
    _publish_claude_event(bus, tmp_path, "iter", {
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_2",
             "content": [
                 {"type": "text", "text": "stdout line 1"},
                 {"type": "text", "text": "stdout line 2"},
                 # Non-text blocks (e.g. images) should be ignored by
                 # the summary — we only join text fragments.
                 {"type": "image", "source": {}},
             ]},
        ]},
    })
    events_seen = list(bus.replay(tmp_path, "iter", since_seq=0))
    assert events_seen[0]["data"]["result"] == (
        "stdout line 1 stdout line 2"
    )


def test_publish_user_tool_result_error_marks_not_ok(tmp_path):
    bus = EventBus()
    _publish_claude_event(bus, tmp_path, "iter", {
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_3",
             "content": "ENOENT", "is_error": True},
        ]},
    })
    events_seen = list(bus.replay(tmp_path, "iter", since_seq=0))
    d = events_seen[0]["data"]
    assert d["ok"] is False
    # When is_error=True the error field carries the same summary so
    # the UI can highlight it.
    assert d["error"] == "ENOENT"


def test_publish_result_event_does_not_emit_turn_end(tmp_path):
    """The terminal `result` event from --print is intentionally
    swallowed — the reader thread's `finally` clause emits the proper
    TurnEndEvent based on exit code so we don't double-end."""
    bus = EventBus()
    _publish_claude_event(bus, tmp_path, "iter", {
        "type": "result", "subtype": "success",
        "is_error": False, "result": "done",
    })
    types = [e["type"] for e in bus.replay(tmp_path, "iter", since_seq=0)]
    assert types == []


def test_publish_unknown_type_does_not_raise(tmp_path):
    bus = EventBus()
    # Forward-compat: a future claude version adding a new event
    # variant must not crash the parser.
    _publish_claude_event(bus, tmp_path, "iter", {
        "type": "future_thing", "data": 42,
    })
    types = [e["type"] for e in bus.replay(tmp_path, "iter", since_seq=0)]
    assert types == []


# ─────────────── _refine_locked: subprocess-stubbed regressions ───────


class _FakeProc:
    """Stand-in for subprocess.Popen — feeds a pre-canned stream to
    the reader thread and reports whatever exit code we set."""

    def __init__(self, stdout_bytes: bytes,
                 *, exit_code: int = 0,
                 hang_forever: bool = False):
        self._lines = stdout_bytes.splitlines(keepends=True)
        self.stdout = _FakeStdout(self._lines, hang_forever=hang_forever)
        self.stdin = _FakeStdin()
        self._exit_code = exit_code
        self._hang = hang_forever
        self.returncode = None
        self.pid = -1

    def wait(self, timeout: Optional[float] = None) -> int:
        if self._hang:
            # Block forever (or until timeout) so we can exercise the
            # _refine_locked timeout path.
            ev = threading.Event()
            ev.wait(timeout=timeout)
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        self.returncode = self._exit_code
        return self._exit_code

    def poll(self) -> Optional[int]:
        return self.returncode

    def terminate(self) -> None:
        # Simulate kill — flip exit code so the reader thread observes
        # a clean exit and signals done_event.
        self.returncode = -15
        self.stdout._wake()

    def kill(self) -> None:
        self.returncode = -9
        self.stdout._wake()


class _FakeStdin:
    def write(self, data: bytes) -> int:
        return len(data)
    def close(self) -> None:
        pass


class _FakeStdout:
    """Iterable-line stdout. ``readline()`` returns the next prepared
    line, then EOF (b"") forever — unless ``hang_forever`` is set, in
    which case it blocks on a condition variable until ``_wake`` is
    called (simulating a wedged subprocess)."""

    def __init__(self, lines, *, hang_forever: bool):
        self._lines = list(lines)
        self._hang = hang_forever
        self._cv = threading.Event()

    def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        if self._hang:
            # Block until terminate() flips the wake event.
            self._cv.wait()
            return b""
        return b""

    def _wake(self) -> None:
        self._cv.set()


def _stage_baseline(workdir: Path, iter_n: int) -> None:
    """Drop a minimal img_iter<N>.png + figure_iter<N>.py pair so
    refine_prompt's baseline-resolution doesn't choke."""
    (workdir / f"img_iter{iter_n}.png").write_bytes(b"\x89PNG\r\n")
    (workdir / f"figure_iter{iter_n}.py").write_text(
        "import matplotlib.pyplot as plt\n"
    )


def _stage_refine_outputs(workdir: Path, n: int,
                          *, rcparams_delta=None, review="ok") -> None:
    """Pretend the agent landed both refine_<N>.{png,json} on disk."""
    rcparams_delta = rcparams_delta or {"font.size": 15}
    (workdir / f"refine_{n:03d}.png").write_bytes(b"\x89PNG\r\nfake")
    (workdir / f"refine_{n:03d}.json").write_text(json.dumps({
        "rcparams_delta": rcparams_delta,
        "review": review,
    }))


def _patch_subprocess(monkeypatch, fake_proc: _FakeProc,
                      output_writer=None):
    """Replace subprocess.Popen at the claude module level so
    _refine_locked spawns our fake instead of a real claude CLI.

    ``output_writer(workdir)`` is invoked once after the fake spawn so
    tests can drop refine_<N>.{png,json} on disk to simulate the
    agent's atomic write.
    """
    spawned = []

    def fake_popen(cmd, **kwargs):
        spawned.append((cmd, kwargs))
        # Eagerly write the agent outputs so by the time the reader
        # thread's finally runs + the runner verifies file presence,
        # they're already there. (Real claude writes them before the
        # `result` event fires.)
        if output_writer is not None:
            cwd = kwargs.get("cwd")
            output_writer(Path(cwd) if cwd else Path("."))
        return fake_proc

    monkeypatch.setattr(claude_mod.subprocess, "Popen", fake_popen)
    return spawned


# ── #1 regression: to_prose called with both required args ─────────


def test_refine_locked_passes_both_args_to_to_prose(tmp_path, monkeypatch):
    """Before the fix, claude.py called ``to_prose(adjustments)`` with
    just one arg — but to_prose's signature is
    ``to_prose(adjustments, message)`` (both positional, no defaults).
    Result: every refine() that supplied adjustments crashed with
    TypeError before the agent was even spawned. Lock that contract."""
    _stage_baseline(tmp_path, 1)

    captured = {}
    real_to_prose = claude_mod.to_prose

    def spy_to_prose(adjustments, message):
        captured["adjustments"] = adjustments
        captured["message"] = message
        return real_to_prose(adjustments, message)

    monkeypatch.setattr(claude_mod, "to_prose", spy_to_prose)

    fake = _FakeProc(b'{"type":"system","subtype":"init",'
                     b'"session_id":"sid-1"}\n', exit_code=0)
    _patch_subprocess(
        monkeypatch, fake,
        output_writer=lambda cwd: _stage_refine_outputs(tmp_path, 1),
    )

    runner = ClaudeRunner()
    out = runner.refine(tmp_path, baseline_iters=[1],
                        adjustments={"font.size": 15})

    # Both args were passed (the bug was the missing second arg).
    assert captured["adjustments"] == {"font.size": 15}
    assert captured["message"] is None
    # The user message was the prose form of the adjustment, not None.
    assert out["set_id"] == compute_set_id([1])


# ── #2 regression: ev_complete.get("seq") on a dataclass ──────────


def test_refine_locked_writes_chat_with_event_bus_seq(tmp_path, monkeypatch):
    """RefineCompleteEvent is a plain @dataclass with NO ``.get``
    method — calling ``ev_complete.get("seq")`` would AttributeError
    after a successful refine, leaving chat.jsonl with a dangling user
    turn and bubbling a 500 to the client. The fix uses the seq the
    bus returns from publish(). Pin both the response and the chat
    log."""
    _stage_baseline(tmp_path, 1)
    fake = _FakeProc(b'{"type":"system","subtype":"init",'
                     b'"session_id":"sid-2"}\n', exit_code=0)
    _patch_subprocess(
        monkeypatch, fake,
        output_writer=lambda cwd: _stage_refine_outputs(tmp_path, 1),
    )

    runner = ClaudeRunner()
    out = runner.refine(tmp_path, baseline_iters=[1], message="bigger")

    # Response shape: seq is a positive int from the bus.
    assert isinstance(out["seq"], int) and out["seq"] >= 1

    # chat.jsonl has both turns and the assistant turn carries the
    # bus-assigned seq (regression: previously this would never get
    # written because .get crashed first).
    from figcopy_runner import chat_log
    turns = chat_log.read_turns(tmp_path, set_id=out["set_id"])
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[1]["seq"] == out["seq"]
    assert turns[1]["image_url"] == "refine_001.png"


def test_refine_locked_accepts_output_before_process_exit(
    tmp_path, monkeypatch,
):
    """If the artifact pair lands before the CLI stream closes, the
    runner should publish success instead of waiting until timeout."""
    _stage_baseline(tmp_path, 1)
    fake = _FakeProc(b'{"type":"system","subtype":"init",'
                     b'"session_id":"sid-file-first"}\n',
                     hang_forever=True)
    _patch_subprocess(
        monkeypatch, fake,
        output_writer=lambda cwd: _stage_refine_outputs(tmp_path, 1),
    )
    monkeypatch.setattr(claude_mod, "REFINE_TURN_TIMEOUT_S", 5.0)

    runner = ClaudeRunner()
    out = runner.refine(tmp_path, baseline_iters=[1], message="bigger")

    assert out["image_url"] == "refine_001.png"
    from figcopy_runner import chat_log
    turns = chat_log.read_turns(tmp_path, set_id=out["set_id"])
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[1]["review"] == "ok"


# ── #3 regression: refine-timeout publishes TurnEndEvent ──────────


def test_refine_locked_publishes_turn_end_on_timeout(tmp_path, monkeypatch):
    """On the timeout path, the runner used to just SIGTERM the slot
    and raise — SSE consumers subscribed to refine:<set_id> never saw
    the turn close, leaving the UI spinner hung. The fix mirrors
    CodexRunner: publish a terminal TurnEndEvent before raising."""
    _stage_baseline(tmp_path, 1)
    # Hang the fake forever so done_event.wait() times out.
    fake = _FakeProc(b'{"type":"system","subtype":"init",'
                     b'"session_id":"sid-3"}\n',
                     hang_forever=True)
    _patch_subprocess(monkeypatch, fake)

    # Shrink the timeout so the test actually completes quickly.
    monkeypatch.setattr(claude_mod, "REFINE_TURN_TIMEOUT_S", 0.2)

    # Subscribe to the slot's bus stream BEFORE the call so we
    # observe the publish-during-timeout in real time.
    bus = claude_mod.event_bus.get_bus()
    set_id = compute_set_id([1])
    slot = f"refine:{set_id}"
    q = bus.subscribe(tmp_path.resolve(), slot)
    try:
        runner = ClaudeRunner()
        with pytest.raises(RefineFailed):
            runner.refine(tmp_path, baseline_iters=[1], message="bigger")
    finally:
        # Drain the queue into a list and assert the timeout TurnEnd
        # is in there.
        seen = []
        while not q.empty():
            seen.append(q.get_nowait())
        bus.unsubscribe(tmp_path.resolve(), slot, q)

    types = [e["type"] for e in seen]
    assert "turn_end" in types, types
    # The turn_end specifically carries the timeout reason.
    end_evt = next(e for e in seen if e["type"] == "turn_end")
    assert end_evt["data"]["status"] == "failed"
    assert end_evt["data"]["reason"] == "timeout_waiting_for_refine_artifacts"
    assert end_evt["data"]["set_id"] == set_id


# ── #4 regression: relative image_url, not absolute /static/... ──


def test_refine_locked_returns_relative_image_url(tmp_path, monkeypatch):
    """interface.Runner.refine documents ``image_url`` as a relative
    path (e.g. ``refine_001.png``) that the server prefixes with
    ``/static/<run>/``. Returning an already-absolute /static/ URL
    works only because the server's rewrite block skips strings
    starting with ``/`` — and it ALSO skips the urlquote that the
    rewrite path applies. Run names with non-URL-safe characters would
    break. CodexRunner returns the relative form; ClaudeRunner now
    matches."""
    _stage_baseline(tmp_path, 1)
    fake = _FakeProc(b'{"type":"system","subtype":"init",'
                     b'"session_id":"sid-4"}\n', exit_code=0)
    _patch_subprocess(
        monkeypatch, fake,
        output_writer=lambda cwd: _stage_refine_outputs(tmp_path, 1),
    )

    runner = ClaudeRunner()
    out = runner.refine(tmp_path, baseline_iters=[1], message="bigger")

    assert out["image_url"] == "refine_001.png"
    # Defensively: the returned value MUST NOT start with "/" so the
    # server's rewrite block actually runs (it skips on `/`-prefix).
    assert not out["image_url"].startswith("/")


# ── PR #25 round-3 regression: lock release on marker-write failure ──


def test_refine_releases_lock_when_marker_write_raises(tmp_path, monkeypatch):
    """PR #25 round-3 finding (lock-leak): the per-(workdir, set_id)
    ``threading.Lock`` is acquired BEFORE ``mark_refine_in_flight`` is
    called. Round-2 had marker-write OUTSIDE the try/finally — if
    ``atomic_write_text`` raised ``OSError`` (ENOSPC / EROFS / EACCES /
    quota / read-only bind-mount), the lock would be acquired but never
    released, permanently wedging this set_id with ``RefineInFlight``
    until process restart.

    The fix moves ``mark_refine_in_flight`` INSIDE the try block so the
    matching ``finally`` releases the lock even when marker-write
    raises. Pin that contract: a marker-write OSError must propagate,
    AND a subsequent ``refine()`` for the same baseline_iters must NOT
    raise ``RefineInFlight`` — the prior lock was released."""
    _stage_baseline(tmp_path, 1)

    calls = {"mark": 0}
    real_mark = claude_mod.mark_refine_in_flight

    def flaky_mark(workdir, set_id, *, refine_idx=None):
        calls["mark"] += 1
        if calls["mark"] == 1:
            raise OSError(28, "No space left on device")
        return real_mark(workdir, set_id, refine_idx=refine_idx)

    monkeypatch.setattr(claude_mod, "mark_refine_in_flight", flaky_mark)

    fake_first = _FakeProc(b"", exit_code=0)
    spawned = _patch_subprocess(monkeypatch, fake_first)
    runner = ClaudeRunner()
    with pytest.raises(OSError):
        runner.refine(tmp_path, baseline_iters=[1], message="bigger")
    # First refine never reached subprocess spawn (marker-write failed
    # first), so spawned must still be empty — proves the regression
    # would also surface if ordering ever moves spawn before mark.
    assert spawned == []

    # Second refine on the SAME set_id MUST succeed: if the lock had
    # leaked, this would raise RefineInFlight before ever reaching the
    # marker code.
    fake_second = _FakeProc(
        b'{"type":"system","subtype":"init","session_id":"sid-recover"}\n',
        exit_code=0,
    )
    _patch_subprocess(
        monkeypatch, fake_second,
        output_writer=lambda cwd: _stage_refine_outputs(tmp_path, 1),
    )
    out = runner.refine(tmp_path, baseline_iters=[1], message="retry")
    assert out["image_url"] == "refine_001.png"
    assert out["set_id"] == compute_set_id([1])
    assert calls["mark"] == 2, (
        "second refine must invoke mark_refine_in_flight again — "
        "proving the per-(workdir, set_id) lock was released after "
        "the first call's OSError."
    )
