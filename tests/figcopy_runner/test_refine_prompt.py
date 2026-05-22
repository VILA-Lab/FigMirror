"""refine_prompt: deterministic, includes history per D12 rules."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from figcopy_runner.interface import reserve_refine_index
from figcopy_runner.refine_prompt import (
    build_system_prompt,
    select_history_entries,
)


def _seed_refine(workdir: Path, n: int, *, delta: dict | None = None,
                 review: str = "", baseline_iters: list[int] | None = None):
    """Write a refine_NNN.{py,json} pair so the prompt sees this as
    a completed prior refine."""
    (workdir / f"refine_{n:03d}.py").write_text(
        f"# code for refine_{n:03d}\nimport matplotlib.pyplot as plt\n"
    )
    (workdir / f"refine_{n:03d}.json").write_text(json.dumps({
        "rcparams_delta": delta or {},
        "review": review,
        "baseline_iters": baseline_iters or [1],
    }))


def _seed_baseline(workdir: Path, iter_n: int, code_body: str = "pass"):
    (workdir / f"img_iter{iter_n}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (workdir / f"figure_iter{iter_n}.py").write_text(code_body)


# ───── select_history_entries rules (D12) ─────────────────────────────


def test_select_history_zero(tmp_path):
    assert select_history_entries(tmp_path) == []


def test_select_history_one(tmp_path):
    _seed_refine(tmp_path, 1)
    out = select_history_entries(tmp_path)
    assert [p.name for p in out] == ["refine_001.py"]


def test_select_history_two(tmp_path):
    _seed_refine(tmp_path, 1)
    _seed_refine(tmp_path, 2)
    out = select_history_entries(tmp_path)
    assert [p.name for p in out] == ["refine_001.py", "refine_002.py"]


def test_select_history_three(tmp_path):
    for n in (1, 2, 3):
        _seed_refine(tmp_path, n)
    out = select_history_entries(tmp_path)
    assert [p.name for p in out] == [
        "refine_001.py", "refine_002.py", "refine_003.py"
    ]


def test_select_history_seven_picks_first_and_last_two(tmp_path):
    for n in range(1, 8):  # refine_001..refine_007
        _seed_refine(tmp_path, n)
    out = select_history_entries(tmp_path)
    assert [p.name for p in out] == [
        "refine_001.py", "refine_006.py", "refine_007.py"
    ]


def test_select_history_four_picks_first_and_last_two(tmp_path):
    for n in (1, 2, 3, 4):
        _seed_refine(tmp_path, n)
    out = select_history_entries(tmp_path)
    assert [p.name for p in out] == [
        "refine_001.py", "refine_003.py", "refine_004.py"
    ]


# ───── build_system_prompt determinism + sections ─────────────────────


def test_build_prompt_is_deterministic(tmp_path):
    _seed_baseline(tmp_path, 2)
    _seed_baseline(tmp_path, 4)
    a = build_system_prompt(
        tmp_path, baseline_iters=[2, 4],
        accumulated_rcparams={"font.size": 13},
        user_message="bigger fonts please",
    )
    b = build_system_prompt(
        tmp_path, baseline_iters=[4, 2],   # different order, same set
        accumulated_rcparams={"font.size": 13},
        user_message="bigger fonts please",
    )
    assert a == b


def test_build_prompt_no_history_section_when_no_prior_refines(tmp_path):
    _seed_baseline(tmp_path, 2)
    out = build_system_prompt(
        tmp_path, baseline_iters=[2],
        accumulated_rcparams={},
        user_message="hi",
    )
    assert "# Recent refine history" not in out


def test_build_prompt_does_not_include_history_section_when_priors_exist(tmp_path):
    """Per user direction (removed after live testing): the prompt no
    longer embeds prior refines as 'recent history' even when they
    exist on disk. They distracted the agent into inheriting style
    choices from refines done on DIFFERENT baseline sets. Anchor is
    the baselines section + accumulated rcParams only."""
    _seed_baseline(tmp_path, 2)
    _seed_refine(tmp_path, 1, delta={"font.size": 13},
                 review="bumped font", baseline_iters=[2])
    _seed_refine(tmp_path, 2, delta={"legend.fontsize": 9},
                 review="smaller legend", baseline_iters=[2])
    _seed_refine(tmp_path, 3, delta={"axes.labelsize": 14},
                 review="larger labels", baseline_iters=[0, 1, 2])
    out = build_system_prompt(
        tmp_path, baseline_iters=[2],
        accumulated_rcparams={"font.size": 13, "legend.fontsize": 9},
        user_message="smaller still",
    )
    # No "Recent refine history" section, no inline refine_NNN.py
    # source dumped into the prompt.
    assert "# Recent refine history" not in out
    assert "refine_001.py" not in out
    assert "refine_002.py" not in out
    assert "refine_003.py" not in out
    assert "bumped font" not in out
    assert "smaller legend" not in out
    assert "larger labels" not in out
    # Accumulated rcParams (filtered to THIS set's history) IS kept —
    # it's not cross-set and tells the agent what state the chat is in.
    assert "font.size" in out
    assert "13" in out


def test_build_prompt_no_history_even_with_seven_priors(tmp_path):
    """Same as above with more prior refines — still nothing embedded."""
    _seed_baseline(tmp_path, 2)
    for n in range(1, 8):
        _seed_refine(tmp_path, n, review=f"review {n}")
    out = build_system_prompt(
        tmp_path, baseline_iters=[2],
        accumulated_rcparams={},
        user_message="...",
    )
    assert "# Recent refine history" not in out
    for n in range(1, 8):
        assert f"refine_{n:03d}.py" not in out
        assert f"review {n}" not in out


def test_build_prompt_baseline_section_orders_by_iter(tmp_path):
    _seed_baseline(tmp_path, 2, "code-for-iter2")
    _seed_baseline(tmp_path, 4, "code-for-iter4")
    out = build_system_prompt(
        tmp_path, baseline_iters=[4, 2],
        accumulated_rcparams={},
        user_message="hi",
    )
    # baseline section should list iter 2 before iter 4 (sorted).
    i2_pos = out.find("iter 2")
    i4_pos = out.find("iter 4")
    assert 0 <= i2_pos < i4_pos


def test_build_prompt_contains_output_contract(tmp_path):
    _seed_baseline(tmp_path, 2)
    out = build_system_prompt(
        tmp_path, baseline_iters=[2],
        accumulated_rcparams={},
        user_message="hi",
    )
    assert "# Output contract" in out
    # Retry-internally + atomicity instructions are non-negotiable.
    assert "Retry" in out or "retry" in out
    assert ".tmp" in out
    assert "refine_001.png" in out
    assert "refine_001.json" in out


def test_build_prompt_user_message_at_end(tmp_path):
    _seed_baseline(tmp_path, 2)
    out = build_system_prompt(
        tmp_path, baseline_iters=[2],
        accumulated_rcparams={},
        user_message="MYUNIQUEUSERMARKER",
    )
    assert out.rstrip().endswith("MYUNIQUEUSERMARKER")


def test_build_prompt_does_not_embed_chat_transcript_or_history(tmp_path):
    """The system prompt MUST NOT embed:
    - User / assistant chat-transcript text (free-form prose, noisy)
    - Prior refine code + review JSON (was the D12 history feature,
      removed because it distracted the agent into inheriting style
      decisions from cross-set refines)
    """
    _seed_baseline(tmp_path, 2)
    _seed_refine(tmp_path, 1, review="MYREVIEWMARKER")
    (tmp_path / "chat.jsonl").write_text(
        '{"role": "user", "content": "MYUSERMESSAGE", "set_id": "s"}\n'
        '{"role": "assistant", "content": "MYASSISTANTMSG", "set_id": "s"}\n'
    )
    out = build_system_prompt(
        tmp_path, baseline_iters=[2],
        accumulated_rcparams={},
        user_message="latest",
    )
    assert "MYUSERMESSAGE" not in out
    assert "MYASSISTANTMSG" not in out
    # Prior refine's review is no longer embedded either.
    assert "MYREVIEWMARKER" not in out
    # User's current-turn message of course IS at the bottom.
    assert "latest" in out


def test_next_refine_index_advances(tmp_path):
    """Output contract must reference the correct next NNN."""
    _seed_baseline(tmp_path, 2)
    # No prior refines → next is 001.
    out1 = build_system_prompt(
        tmp_path, baseline_iters=[2],
        accumulated_rcparams={}, user_message="m",
    )
    assert "refine_001.png" in out1
    # After 2 priors → next is 003.
    _seed_refine(tmp_path, 1)
    _seed_refine(tmp_path, 2)
    out3 = build_system_prompt(
        tmp_path, baseline_iters=[2],
        accumulated_rcparams={}, user_message="m",
    )
    assert "refine_003.png" in out3
    assert "refine_003.json" in out3


def test_output_contract_uses_reserved_index_when_provided(tmp_path):
    """Runner passes its reservation so the prompt and watcher agree."""
    _seed_baseline(tmp_path, 2)
    reserved = reserve_refine_index(tmp_path, "deadbeef")

    out = build_system_prompt(
        tmp_path, baseline_iters=[2],
        accumulated_rcparams={}, user_message="m",
        expected_refine_index=reserved,
    )

    assert reserved == 1
    assert "refine_001.png" in out
    assert "refine_002.png" not in out
