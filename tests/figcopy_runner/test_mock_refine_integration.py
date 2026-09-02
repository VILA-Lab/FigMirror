"""MockRunner.refine + helpers wire-up integration test.

Confirms that calling MockRunner.refine produces:
- refine_NNN.png + refine_NNN.json atomically
- chat.jsonl entries (user + assistant) indexed by set_id
- sessions.json entry under refine.<set_id>
- a published RefineCompleteEvent on the event bus

Exercises the Stage-A substrate end-to-end on the mock backend so
Stage B/C can plug real subprocesses into the same plumbing.
"""
from __future__ import annotations

import json
import time

import pytest

from figcopy_runner import chat_log
from figcopy_runner.interface import compute_set_id, read_sessions
from figcopy_runner.mock import MockRunner


def _seed_baseline_iter(workdir, iter_n: int):
    """Create a minimal img_iter<N>.png + figure_iter<N>.py pair so
    MockRunner's refine has something to overlay."""
    # A 4×4 white PNG (smallest valid PNG that Pillow can open).
    from PIL import Image
    img = Image.new("RGB", (32, 32), "white")
    img.save(workdir / f"img_iter{iter_n}.png", format="PNG")
    (workdir / f"figure_iter{iter_n}.py").write_text(
        "import matplotlib.pyplot as plt\nfig = plt.figure()\n"
    )


def test_mock_refine_first_turn_writes_all_artifacts(tmp_path):
    _seed_baseline_iter(tmp_path, 2)
    _seed_baseline_iter(tmp_path, 4)
    runner = MockRunner()

    result = runner.refine(
        tmp_path,
        baseline_iters=[2, 4],
        message="字大一点",
    )

    expected_set_id = compute_set_id([2, 4])
    # Response shape per backend spec.
    assert result["set_id"] == expected_set_id
    assert result["image_url"] == "refine_001.png"
    assert isinstance(result["rcparams_delta"], dict)
    assert isinstance(result["review"], str)
    assert isinstance(result["seq"], int) and result["seq"] >= 1

    # On disk: png + json + chat.jsonl + sessions.json all present.
    assert (tmp_path / "refine_001.png").exists()
    assert (tmp_path / "refine_001.json").exists()
    assert (tmp_path / "chat.jsonl").exists()
    assert (tmp_path / "sessions.json").exists()
    # No leftover .tmp files (atomic discipline).
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())

    # sessions.json has the new refine session-id.
    sessions = read_sessions(tmp_path)
    assert expected_set_id in sessions["refine"]

    # chat.jsonl has exactly 2 entries for this set (user + assistant).
    turns = chat_log.read_turns(tmp_path, set_id=expected_set_id)
    roles = [t["role"] for t in turns]
    assert roles == ["user", "assistant"]
    assert turns[0]["baseline_iters"] == [2, 4]
    # Assistant entry carries the image_url + delta + review.
    asst = turns[1]
    assert asst["image_url"] == "refine_001.png"
    assert asst["rcparams_delta"] == result["rcparams_delta"]
    assert asst["review"] == result["review"]
    assert asst["seq"] == result["seq"]


def test_mock_refine_multi_turn_increments_index_and_shares_set(tmp_path):
    _seed_baseline_iter(tmp_path, 1)
    runner = MockRunner()
    set_id = compute_set_id([1])

    r1 = runner.refine(tmp_path, baseline_iters=[1], message="bigger")
    r2 = runner.refine(tmp_path, baseline_iters=[1], message="smaller")
    r3 = runner.refine(tmp_path, baseline_iters=[1], message="legend off")

    assert r1["set_id"] == r2["set_id"] == r3["set_id"] == set_id
    assert (r1["image_url"], r2["image_url"], r3["image_url"]) == (
        "refine_001.png", "refine_002.png", "refine_003.png"
    )
    # seq is monotonic for this session key.
    assert r1["seq"] < r2["seq"] < r3["seq"]

    # chat.jsonl has 6 lines (3 user + 3 assistant) all under same set.
    turns = chat_log.read_turns(tmp_path, set_id=set_id)
    assert len(turns) == 6
    assert [t["role"] for t in turns] == [
        "user", "assistant", "user", "assistant", "user", "assistant"
    ]


def test_mock_refine_axis_label_fonts_larger_targets_axis_labels(tmp_path):
    _seed_baseline_iter(tmp_path, 1)
    runner = MockRunner()

    result = runner.refine(
        tmp_path,
        baseline_iters=[1],
        message="now make the axis label fonts larger",
    )

    assert result["rcparams_delta"] == {"axes.labelsize": 15}
    payload = json.loads((tmp_path / "refine_001.json").read_text())
    assert payload["rcparams_delta"] == {"axes.labelsize": 15}
    assert "axis labels read larger" in result["review"]


def test_mock_refine_distinct_sets_get_distinct_sessions(tmp_path):
    _seed_baseline_iter(tmp_path, 1)
    _seed_baseline_iter(tmp_path, 2)
    runner = MockRunner()

    sid_a = compute_set_id([1])
    sid_b = compute_set_id([1, 2])

    runner.refine(tmp_path, baseline_iters=[1], message="a")
    runner.refine(tmp_path, baseline_iters=[1, 2], message="b")

    sessions = read_sessions(tmp_path)
    assert sid_a in sessions["refine"]
    assert sid_b in sessions["refine"]
    assert sessions["refine"][sid_a] != sessions["refine"][sid_b]


def test_mock_refine_adjustments_become_prose(tmp_path):
    _seed_baseline_iter(tmp_path, 1)
    runner = MockRunner()
    result = runner.refine(
        tmp_path,
        baseline_iters=[1],
        adjustments={"font.size": 15},
    )
    # chat.jsonl's user line records the prose form.
    turns = chat_log.read_turns(tmp_path)
    user_line = turns[0]
    assert user_line["content"].startswith("Adjust: font.size = 15")
    # And keeps the raw adjustments dict for audit.
    assert user_line["adjustments"] == {"font.size": 15}
    # Delta echoes the adjustment.
    assert result["rcparams_delta"] == {"font.size": 15}


def test_mock_refine_rejects_empty_baseline_iters(tmp_path):
    runner = MockRunner()
    with pytest.raises(ValueError):
        runner.refine(tmp_path, baseline_iters=[])


def test_mock_auto_mode_still_respects_hard_iteration_cap(tmp_path):
    runner = MockRunner(sleep_min=0, sleep_max=0)
    runner.start(tmp_path, max_iters=1, auto=True)

    deadline = time.monotonic() + 2
    while runner.status(tmp_path)["state"] == "running":
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert (tmp_path / "img_iter0.png").is_file()
    assert not (tmp_path / "img_iter1.png").exists()
