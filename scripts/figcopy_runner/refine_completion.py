"""Helpers for robust Step-2 refine completion.

Real CLI agents can land the promised ``refine_NNN.{png,json}`` pair
before their streaming process closes. The UI should treat those final
artifacts as the source of truth, rather than waiting only for process
exit and risking a false timeout.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path


POLL_INTERVAL_S = 0.5


@dataclass(frozen=True)
class RefineOutput:
    png_path: Path
    json_path: Path
    outcome: dict


def try_read_refine_output(workdir: Path, refine_idx: int) -> RefineOutput | None:
    """Return the completed refine output if both final files are ready.

    ``None`` means "not ready yet" rather than failure. Atomic writers
    may briefly expose only one final file, or the process may still be
    rendering, so callers should poll until either this returns an
    output or the subprocess exits.
    """
    png_path = workdir / f"refine_{refine_idx:03d}.png"
    json_path = workdir / f"refine_{refine_idx:03d}.json"
    if not (png_path.is_file() and json_path.is_file()):
        return None
    try:
        if png_path.stat().st_size <= 0:
            return None
        outcome = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(outcome, dict):
        return None
    return RefineOutput(png_path=png_path, json_path=json_path, outcome=outcome)


def wait_for_refine_output_or_done(
    workdir: Path,
    refine_idx: int,
    done_event: threading.Event,
    *,
    timeout_s: float | None = None,
    poll_interval_s: float = POLL_INTERVAL_S,
) -> tuple[RefineOutput | None, bool]:
    """Wait until output files land or the process reader finishes.

    Returns ``(output, reader_done)``. ``output`` is authoritative when
    present, even if ``reader_done`` is false. ``timeout_s`` exists for
    fault-injection tests and optional caller policies; production
    refine turns pass ``None`` so a slow-but-live agent can finish.
    """
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while True:
        output = try_read_refine_output(workdir, refine_idx)
        if output is not None:
            return output, done_event.is_set()
        if done_event.is_set():
            return None, True
        if deadline is None:
            done_event.wait(timeout=poll_interval_s)
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, False
        done_event.wait(timeout=min(poll_interval_s, remaining))


def salvage_refine_output_from_tmp(
    workdir: Path,
    refine_idx: int,
    *,
    grace_period_s: float = 1.0,
    poll_interval_s: float = 0.1,
) -> RefineOutput | None:
    """Last-chance check for a completed refine artifact pair.

    Called by the runner after the wait loop returned ``None`` and
    after the agent subprocess has been terminated. The agent may have
    finished writing ``refine_NNN.{png,json}`` in the small race
    window between ``wait_for_refine_output_or_done`` returning and
    the kill landing — give the filesystem a brief grace period for
    any in-flight atomic ``.tmp + rename`` to settle, then re-check.

    PR #25 round-2 finding #3 / round-1 codex follow-up P1: the
    original implementation in this slot subprocess-executed
    ``refine_NNN.py`` (renamed from ``.tmp``) under the server's
    ``sys.executable`` with no sandbox, while the upstream CLI runs
    under ``--sandbox workspace-write`` (codex) / ``--add-dir``
    (claude). Agent-authored script content is in the prompt-injection
    threat model; running it with ambient process privileges on the
    salvage path was a real escape hatch.

    Replaced (PR #25 round-2 fix) with the option-(d) recovery from
    review comment 4439770316: refuse to execute, just look for the
    artifact pair the original agent might have produced. This matches
    what ``chat_log.recover_orphan_refines`` does for the non-timeout
    crash variant. Same correctness invariant, smaller blast radius:
    we never subprocess-execute agent-authored Python on a salvage
    path.

    PR #25 round-2 finding #2 (``.tmp`` freshness gate) collapses
    under this rewrite: there is no script to execute, so the
    "is the script from THIS turn?" question is moot. Cross-turn
    correctness is instead protected by ``_next_refine_index`` —
    which counts ``refine_*.json`` files and so cannot reuse an index
    whose JSON already exists from a prior turn.

    ``grace_period_s`` defaults to 1s, polling every 100 ms. Callers
    in real-runner timeout paths can pass 0 to skip the grace if they
    just want a single-shot disk check.
    """
    deadline = time.monotonic() + max(grace_period_s, 0.0)
    while True:
        output = try_read_refine_output(workdir, refine_idx)
        if output is not None:
            return output
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(poll_interval_s, remaining))
