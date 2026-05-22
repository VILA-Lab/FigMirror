"""refine_completion: file-backed Step-2 success detection."""
from __future__ import annotations

import json
import threading

from figcopy_runner.refine_completion import (
    salvage_refine_output_from_tmp,
    try_read_refine_output,
    wait_for_refine_output_or_done,
)


def test_try_read_refine_output_requires_png_and_parseable_json(tmp_path):
    assert try_read_refine_output(tmp_path, 1) is None

    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text("not json")
    assert try_read_refine_output(tmp_path, 1) is None

    (tmp_path / "refine_001.json").write_text(json.dumps({
        "review": "ok",
        "rcparams_delta": {"font.size": 14},
    }))
    output = try_read_refine_output(tmp_path, 1)

    assert output is not None
    assert output.png_path.name == "refine_001.png"
    assert output.outcome["review"] == "ok"


def test_wait_returns_output_before_process_done(tmp_path):
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({"review": "ok"}))
    done_event = threading.Event()

    output, reader_done = wait_for_refine_output_or_done(
        tmp_path,
        1,
        done_event,
        timeout_s=1.0,
    )

    assert output is not None
    assert reader_done is False


def test_wait_returns_done_without_output(tmp_path):
    done_event = threading.Event()
    done_event.set()

    output, reader_done = wait_for_refine_output_or_done(
        tmp_path,
        1,
        done_event,
        timeout_s=1.0,
    )

    assert output is None
    assert reader_done is True


def test_wait_without_timeout_waits_for_artifact_pair(tmp_path):
    done_event = threading.Event()

    def write_output() -> None:
        (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
        (tmp_path / "refine_001.json").write_text(json.dumps({
            "review": "ok",
            "baseline_iters": [0],
        }))

    writer = threading.Timer(0.05, write_output)
    writer.start()
    try:
        output, reader_done = wait_for_refine_output_or_done(
            tmp_path,
            1,
            done_event,
            poll_interval_s=0.01,
        )
    finally:
        writer.join(timeout=1.0)

    assert output is not None
    assert output.outcome["review"] == "ok"
    assert reader_done is False


# ─── PR #25 round-2 regressions ───────────────────────────────────────


def test_salvage_returns_existing_artifact_pair_without_executing(tmp_path):
    """PR #25 round-2 finding #3 (sandbox-bypass): the salvage path
    MUST NOT subprocess-execute agent-authored Python. It just looks
    for the artifact pair the original agent might have produced
    before the upstream wait loop gave up. If the pair is on disk,
    surface it; otherwise return None.

    Pin the canonical happy-path: the agent finished writing
    ``refine_NNN.png`` + ``refine_NNN.json`` (so the prompt's "Do NOT
    leave any .tmp file behind" contract is honored — no .tmp on
    disk), but the runner timed out waiting for the streaming process
    to close. Salvage MUST recover the pair from disk."""
    (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
    (tmp_path / "refine_001.json").write_text(json.dumps({
        "review": "salvaged from on-disk pair",
        "rcparams_delta": {"font.size": 14},
        "baseline_iters": [5],
    }))

    output = salvage_refine_output_from_tmp(
        tmp_path, 1, grace_period_s=0,
    )

    assert output is not None
    assert output.png_path.name == "refine_001.png"
    assert output.outcome["review"] == "salvaged from on-disk pair"


def test_salvage_does_not_execute_leftover_script(tmp_path):
    """PR #25 round-2 finding #3 (sandbox-bypass) AND round-2 finding
    #2 (.tmp gate is unsatisfiable per the agent contract): a leftover
    ``refine_NNN.py`` (with or without a sibling ``.tmp``) MUST NOT be
    subprocess-executed by the salvage path. Without the artifact pair
    on disk, salvage returns None. The script's mere presence is not
    a license to run unsandboxed agent-authored Python under the
    server's process privileges.

    If this regresses (someone re-introduces the ``subprocess.run`` of
    ``refine_NNN.py``), the script below would write a STALE PNG/JSON
    and the test would observe a non-None salvage result."""
    (tmp_path / "refine_001.py").write_text(
        "from pathlib import Path\n"
        "import json\n"
        "here = Path(__file__).resolve().parent\n"
        "(here / 'refine_001.png').write_bytes(b'STALE-PNG-from-exec')\n"
        "(here / 'refine_001.json').write_text(json.dumps({\n"
        "    'review': 'STALE — script must not be executed',\n"
        "    'baseline_iters': [5],\n"
        "}))\n",
        encoding="utf-8",
    )
    # Also stage a .tmp sibling — pre-fix this would have triggered
    # the .tmp + rename + execute path.
    (tmp_path / "refine_001.py.tmp").write_text(
        "from pathlib import Path\n"
        "import json\n"
        "here = Path(__file__).resolve().parent\n"
        "(here / 'refine_001.png').write_bytes(b'STALE-PNG-from-tmp-exec')\n"
        "(here / 'refine_001.json').write_text(json.dumps({\n"
        "    'review': 'STALE — .tmp must not be executed',\n"
        "    'baseline_iters': [5],\n"
        "}))\n",
        encoding="utf-8",
    )

    output = salvage_refine_output_from_tmp(
        tmp_path, 1, grace_period_s=0,
    )

    assert output is None, (
        "PR #25 round-2 finding #3: salvage MUST NOT subprocess-execute "
        "agent-authored Python. With no artifact pair on disk the "
        "salvage path returns None."
    )
    # Critically: the agent-authored script must NOT have run.
    assert not (tmp_path / "refine_001.png").exists()
    assert not (tmp_path / "refine_001.json").exists()


def test_salvage_grace_period_polls_for_in_flight_atomic_rename(tmp_path):
    """PR #25 round-2 finding #3 secondary contract: the grace period
    exists so that an agent's in-flight ``.tmp + rename`` to the final
    artifact name has a chance to land in the small race window
    between ``wait_for_refine_output_or_done`` returning and the kill
    signal taking effect. Pin that a pair landing during the grace
    window IS recovered."""
    import time as _time

    def _land_pair_after(delay_s: float) -> None:
        _time.sleep(delay_s)
        (tmp_path / "refine_001.png").write_bytes(b"\x89PNG\r\nfake")
        (tmp_path / "refine_001.json").write_text(json.dumps({
            "review": "landed during grace window",
            "baseline_iters": [5],
        }))

    t = threading.Thread(target=_land_pair_after, args=(0.05,))
    t.start()
    try:
        output = salvage_refine_output_from_tmp(
            tmp_path, 1,
            grace_period_s=2.0,
            poll_interval_s=0.05,
        )
    finally:
        t.join(timeout=5.0)

    assert output is not None
    assert output.outcome["review"] == "landed during grace window"


def test_salvage_does_not_invoke_subprocess(tmp_path, monkeypatch):
    """PR #25 round-2 finding #3 belt-and-suspenders: assert no
    subprocess module call is made on the salvage path, in either the
    "no artifacts on disk" or "artifacts on disk" case. If anyone
    reintroduces ``subprocess.run`` / ``Popen`` to "complete the
    script" the test fails immediately."""
    import subprocess as _subprocess

    sentinel = {"called": False}

    def _no_subprocess(*args, **kwargs):
        sentinel["called"] = True
        raise AssertionError(
            "salvage path invoked subprocess — sandbox-bypass regression"
        )

    monkeypatch.setattr(_subprocess, "run", _no_subprocess)
    monkeypatch.setattr(_subprocess, "Popen", _no_subprocess)

    # No artifacts on disk → returns None without subprocess.
    assert salvage_refine_output_from_tmp(
        tmp_path, 1, grace_period_s=0,
    ) is None
    # With artifacts on disk → returns them without subprocess.
    (tmp_path / "refine_002.png").write_bytes(b"\x89PNG")
    (tmp_path / "refine_002.json").write_text(
        json.dumps({"review": "ok", "baseline_iters": [5]})
    )
    output = salvage_refine_output_from_tmp(
        tmp_path, 2, grace_period_s=0,
    )
    assert output is not None
    assert sentinel["called"] is False
