"""codex.py pure-helper tests.

The live subprocess + reader-thread paths are covered by an
end-to-end smoke run; here we just lock in the event-translation
contract (codex JSONL → SessionEvent union) which is the most
fragile part of the runner.
"""
from __future__ import annotations

import pytest

from figcopy_runner import codex as codex_mod
from figcopy_runner import claude as claude_mod
from figcopy_runner import events as ev
from figcopy_runner.codex import (
    CodexRunner,
    RefineInFlight,
    _parse_iter_n_from_path,
    _parse_jsonl_line,
    _publish_codex_event,
    _next_refine_index,
    _accumulated_rcparams_for_set,
)
from figcopy_runner.event_bus import EventBus
from figcopy_runner.interface import (
    compute_set_id,
    ensure_reference_raw,
    reviewer_protocol_failure_reason,
)


def test_parse_iter_n_basic():
    assert _parse_iter_n_from_path("/x/img_iter3.png") == 3
    assert _parse_iter_n_from_path("img_iter12.png") == 12


def test_parse_iter_n_misses_non_iter_files():
    assert _parse_iter_n_from_path("figure_iter3.py") is None
    assert _parse_iter_n_from_path("img.png") is None
    assert _parse_iter_n_from_path("refine_001.png") is None


def test_loop_policy_respects_max_iters_by_default():
    policy = codex_mod._format_loop_policy(max_iters=4, auto=False)
    assert "Maximum iterations: 4" in policy
    assert "N = 0..3" in policy
    assert "Auto-until-shipped" not in policy


def test_loop_policy_auto_ignores_default_cap_for_both_backends():
    codex_policy = codex_mod._format_loop_policy(max_iters=4, auto=True)
    claude_policy = claude_mod._format_loop_policy(max_iters=4, auto=True)
    assert codex_policy == claude_policy
    assert "Auto-until-shipped is enabled" in codex_policy
    assert "Maximum iterations" not in codex_policy
    assert "`ship`" in codex_policy


def test_reference_preprocessor_prompt_synced_between_backends(monkeypatch, tmp_path):
    """Both backends must hand the model the SAME prompt body — no
    YAML frontmatter, no leading ``---`` fence. The Claude loader
    strips frontmatter (Adv #1, PR-27 round 1) so the assertion is
    exact equality with no munging here.
    """
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex-home"))
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path / "no-claude-home"))
    codex_prompt = codex_mod._load_reference_preprocessor_prompt()
    claude_prompt = claude_mod._load_reference_preprocessor_prompt()
    assert not claude_prompt.startswith("---"), (
        "Claude preprocessor loader must strip YAML frontmatter so "
        "the model is not handed `tools:`/`model:` lines as part of "
        "the prompt body."
    )
    assert codex_prompt == claude_prompt
    assert "reference_raw.png" in codex_prompt
    assert "reference_crop_check.png" in codex_prompt


def test_step1_prompts_invoke_native_figmirror_skill():
    """Step 1 must route through the installed FigMirror skill.

    Regression guard for PR #27's staged-prompt detour: the Web UI
    runner should not bypass skill dispatch by telling the model to
    read workdir-local prompt files.
    """
    for template in (
        codex_mod._STEP1_PROMPT_TEMPLATE,
        claude_mod._STEP1_PROMPT_TEMPLATE,
    ):
        assert "Use `$figmirror`" in template
        assert "user-level" in template
        assert "prompt files already staged" not in template
        assert "global installed skill copy" not in template


def test_runner_prompts_prefer_installed_skill_paths(monkeypatch, tmp_path):
    codex_home = tmp_path / "codex-home"
    codex_skill = codex_home / "skills" / "figmirror"
    codex_skill.mkdir(parents=True)
    (codex_skill / "SKILL.md").write_text("---\nname: figmirror\n---\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    claude_home = tmp_path / "claude-home"
    claude_skill = claude_home / "skills" / "figmirror"
    claude_agents = claude_home / "agents"
    claude_skill.mkdir(parents=True)
    claude_agents.mkdir(parents=True)
    (claude_skill / "SKILL.md").write_text("---\nname: figmirror\n---\n")
    (claude_agents / "figure-preprocessor.md").write_text("prompt")
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))

    codex_prompt = codex_mod._STEP1_PROMPT_TEMPLATE.format(
        workdir="/tmp/run",
        skill_dir=codex_mod._codex_skill_dir_for_prompt(),
        max_iters=1,
        loop_policy="Loop policy",
        user_request="Make a figure.",
    )
    claude_prompt = claude_mod._STEP1_PROMPT_TEMPLATE.format(
        workdir="/tmp/run",
        skill_dir=claude_mod._claude_skill_dir_for_prompt(),
        agents_dir=claude_mod._claude_agents_dir_for_prompt(),
        max_iters=1,
        loop_policy="Loop policy",
        user_request="Make a figure.",
    )

    assert str(codex_skill.resolve()) in codex_prompt
    assert str(claude_skill.resolve()) in claude_prompt
    assert str(claude_agents.resolve()) in claude_prompt


def test_ensure_reference_raw_preserves_legacy_clean_upload(tmp_path):
    wd = tmp_path / "run"
    inputs = wd / "inputs"
    inputs.mkdir(parents=True)
    clean = inputs / "reference_clean.png"
    clean.write_bytes(b"legacy-upload")

    raw = ensure_reference_raw(wd)

    assert raw == inputs / "reference_raw.png"
    assert raw.read_bytes() == b"legacy-upload"
    assert clean.read_bytes() == b"legacy-upload"


def test_parse_jsonl_line_handles_garbage():
    assert _parse_jsonl_line(b"") is None
    assert _parse_jsonl_line(b"\n") is None
    assert _parse_jsonl_line(b"not json") is None
    assert _parse_jsonl_line(b'"a string"') is None
    assert _parse_jsonl_line(b'{"type": "x"}')["type"] == "x"


def test_publish_thread_started(tmp_path):
    bus = EventBus()
    out = _publish_codex_event(bus, tmp_path, "iter", {
        "type": "thread.started",
        "thread_id": "abc-uuid",
    })
    assert out == ("thread", "abc-uuid")


def test_publish_agent_message_emits_text(tmp_path):
    bus = EventBus()
    _publish_codex_event(bus, tmp_path, "refine:s", {
        "type": "item.completed",
        "item": {"id": "item_0", "type": "agent_message",
                 "text": "hello world"},
    })
    # Buffer should contain one TextEvent.
    events_seen = list(bus.replay(tmp_path, "refine:s", since_seq=0))
    assert [e["type"] for e in events_seen] == ["text"]
    assert events_seen[0]["data"]["text"] == "hello world"


def test_publish_file_change_iter_returns_iter_complete_tuple(tmp_path):
    """`_publish_codex_event` SIGNALS an iter completion via the
    returned tuple but does NOT publish the IterCompleteEvent itself —
    the caller (_reader_iter) does the publish under a claim-and-
    publish lock so it can dedupe against the file-poll thread. This
    test locks in that contract."""
    bus = EventBus()
    out = _publish_codex_event(bus, tmp_path, "iter", {
        "type": "item.completed",
        "item": {
            "id": "item_1", "type": "file_change", "status": "completed",
            "changes": [{"path": "/x/img_iter4.png", "kind": "add"}],
        },
    })
    assert out == ("iter_complete", 4)
    types = [e["type"] for e in bus.replay(tmp_path, "iter", since_seq=0)]
    # tool_call_end DOES fire (we always normalize item.completed into
    # the tool-call-end event for non-message items).
    assert "tool_call_end" in types
    # iter_complete is NOT emitted here — the caller owns that publish.
    assert "iter_complete" not in types


def test_publish_file_change_non_iter_no_iter_complete(tmp_path):
    bus = EventBus()
    out = _publish_codex_event(bus, tmp_path, "iter", {
        "type": "item.completed",
        "item": {
            "id": "item_1", "type": "file_change", "status": "completed",
            "changes": [{"path": "/x/notes_iter4.md", "kind": "add"}],
        },
    })
    assert out is None
    types = [e["type"] for e in bus.replay(tmp_path, "iter", since_seq=0)]
    assert "tool_call_end" in types
    assert "iter_complete" not in types


def test_publish_unknown_type_does_not_raise(tmp_path):
    bus = EventBus()
    out = _publish_codex_event(bus, tmp_path, "iter", {
        "type": "what_is_this",
        "extra": 42,
    })
    assert out is None


def test_next_refine_index_empty(tmp_path):
    assert _next_refine_index(tmp_path) == 1


def test_next_refine_index_existing(tmp_path):
    (tmp_path / "refine_001.json").write_text("{}")
    (tmp_path / "refine_002.json").write_text("{}")
    (tmp_path / "refine_005.json").write_text("{}")
    assert _next_refine_index(tmp_path) == 6


def test_accumulated_rcparams_replays_assistant_entries(tmp_path):
    from figcopy_runner import chat_log
    chat_log.append_turn(tmp_path, role="user", content="bigger",
                         set_id="A", baseline_iters=[1])
    chat_log.append_turn(tmp_path, role="assistant", content="ok",
                         set_id="A", baseline_iters=[1],
                         rcparams_delta={"font.size": 13})
    chat_log.append_turn(tmp_path, role="user", content="legend off",
                         set_id="A", baseline_iters=[1])
    chat_log.append_turn(tmp_path, role="assistant", content="ok2",
                         set_id="A", baseline_iters=[1],
                         rcparams_delta={"legend.frameon": False})
    # Other set's entries should NOT contaminate.
    chat_log.append_turn(tmp_path, role="assistant", content="other",
                         set_id="B", baseline_iters=[2],
                         rcparams_delta={"axes.labelsize": 99})

    acc = _accumulated_rcparams_for_set(tmp_path, "A")
    assert acc == {"font.size": 13, "legend.frameon": False}


def test_reviewer_protocol_failure_detects_local_audit_fallback(tmp_path):
    (tmp_path / "audit_iter0.stderr").write_text(
        "codex exec reviewer unavailable; local reviewer JSON written "
        "with skill schema\n"
    )

    reason = reviewer_protocol_failure_reason(tmp_path)

    assert reason is not None
    assert "fresh-context reviewer failed" in reason


def test_reviewer_protocol_failure_ignores_empty_stderr(tmp_path):
    (tmp_path / "audit_iter0.stderr").write_text("")

    assert reviewer_protocol_failure_reason(tmp_path) is None


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
    raises. We assert the contract directly via the runner's internal
    lock state — calling the full ``refine()`` path would require
    stubbing the codex CLI subprocess, which CodexRunner intentionally
    doesn't expose; the lock-release invariant is independent of the
    subprocess and can be exercised by stubbing the marker call alone."""

    def boom(workdir, set_id, *, refine_idx=None):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(codex_mod, "mark_refine_in_flight", boom)

    runner = CodexRunner()
    set_id = compute_set_id([1])

    with pytest.raises(OSError):
        runner.refine(tmp_path, baseline_iters=[1], message="bigger")

    # The per-(workdir, set_id) lock must have been released — its
    # ``acquire(blocking=False)`` should now succeed. If the round-3
    # fix regressed (i.e., mark_refine_in_flight moved back outside
    # the try block), this would block / return False because the
    # lock was leaked.
    leaked_lock = runner._get_refine_lock(tmp_path.resolve(), set_id)
    assert leaked_lock.acquire(blocking=False) is True, (
        "PR #25 round-3 finding: per-set lock leaked when "
        "mark_refine_in_flight raised OSError. The fix is to call "
        "mark_refine_in_flight INSIDE the try block so the matching "
        "finally releases the lock."
    )
    leaked_lock.release()
