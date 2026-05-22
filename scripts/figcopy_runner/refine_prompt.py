"""refine_prompt — build the Step-2 inline system prompt.

Per design.md §D4 (inline prompt, not skill) + §D11 (agent retries
matplotlib internally) + §D12 (compressed history of prior refines).

The prompt is deterministic: same workdir state + same args → byte-
identical output. This is what makes the function testable via golden
strings.

Structure of the produced prompt:

    # Role
        You are a matplotlib figure refiner.

    # Baselines (selected by user)
        Reference 1: img_iter2.png + figure_iter2.py
        Reference 2: img_iter4.png + figure_iter4.py
        ...

    # Accumulated rcParams so far (this set, this run)
        {...}

    # Recent refine history on this run (for context)
        (Optional section; included per D12 rules)
        Entry 1 — refine_001 (from baselines [2, 4]):
            code: ...
            review: ...
            rcparams_delta: {...}
        ...

    # Output contract
        Write `refine_NNN.png` and `refine_NNN.json` atomically.
        json schema: {"rcparams_delta": dict, "review": str}.
        Retry internally until png renders successfully.
        Do NOT declare done until both files land via .tmp + rename.

    # User request
        <message>

The user's latest message is the only non-deterministic input from
the runner's perspective; everything else is derived from on-disk
workdir state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .interface import next_refine_index


# ───── history selection per D12 ──────────────────────────────────────


def select_history_entries(workdir: Path) -> list[Path]:
    """Pick `refine_NNN.py` files for inclusion in the prompt history.

    Rule (per design.md §D12):

    - 0 prior refines → return ``[]``
    - 1, 2, or 3 prior refines → return all of them, oldest first
    - 4+ prior refines → return ``[first, second_to_last, last]``

    The function returns ``.py`` paths; callers separately load the
    matching ``.json`` to get the review + delta. ``.png`` is never
    included in the prompt (just the path is referenced in the
    baseline section).
    """
    workdir = workdir.resolve()
    pys = sorted(workdir.glob("refine_*.py"))
    if not pys:
        return []
    if len(pys) <= 3:
        return list(pys)
    return [pys[0], pys[-2], pys[-1]]


def _read_text_safe(path: Path) -> Optional[str]:
    """Read a file's text or return None if missing/unreadable."""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _read_json_safe(path: Path) -> Optional[dict]:
    """Read a JSON file or return None if missing/malformed."""
    text = _read_text_safe(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ───── prompt builder ─────────────────────────────────────────────────


def build_system_prompt(workdir: Path, *,
                        baseline_iters: list[int],
                        accumulated_rcparams: dict,
                        user_message: str,
                        expected_refine_index: Optional[int] = None) -> str:
    """Assemble the Step-2 turn-1 system prompt.

    Deterministic: identical inputs (and identical workdir contents)
    produce identical output. The ``user_message`` is included at the
    end so a single function returns the *complete* turn-1 prompt the
    runner passes to the CLI.

    For follow-up turns (turn 2+) the runner does NOT call this — it
    just passes the new user message via ``--resume <sid>``.
    """
    workdir = workdir.resolve()
    baselines_sorted = sorted(set(baseline_iters))

    parts: list[str] = []

    parts.append(
        "# Role\n"
        "You are a matplotlib figure refiner. The user has chosen one\n"
        "or more reference figures (baselines) and wants you to produce\n"
        "a single new figure that matches their refinement request,\n"
        "drawing style ideas from the baselines as the user describes.\n"
        "\n"
        "Your output MUST be derived from the baseline code(s) below.\n"
        f"The baselines for THIS turn are: iters {baselines_sorted}."
    )

    # Baselines section.
    parts.append(
        "# Baselines for THIS turn (anchor your output here)\n"
        "These are the user's chosen reference figures for this chat."
    )
    baseline_lines = []
    for idx_in_set, iter_n in enumerate(baselines_sorted, start=1):
        png_path = workdir / f"img_iter{iter_n}.png"
        py_path = workdir / f"figure_iter{iter_n}.py"
        py_src = _read_text_safe(py_path)
        baseline_lines.append(
            f"## Reference {idx_in_set} (iter {iter_n})\n"
            f"image: {png_path}\n"
            f"code:\n```python\n{py_src or '(figure_iter%s.py missing)' % iter_n}\n```"
        )
    parts.append("\n\n".join(baseline_lines))

    # Accumulated rcparams section.
    # Kept because: (a) it's filtered to THIS set's chat history (not
    # cross-set, so no distraction risk), (b) on a resumed thread the
    # agent already knows them from transcript so this is redundant
    # but harmless, (c) it's an empty dict on turn-1 of a new set so
    # also harmless then.
    #
    # NOTE: an earlier draft of this prompt ALSO embedded a
    # "Recent refine history on this RUN" section listing
    # refine_NNN.{py,json} files from OTHER baseline sets, per D12.
    # In live testing the agent inherited palette/layout decisions
    # from those cross-set entries even with explicit "informational
    # only" labels — the user reported wrong colors after switching
    # baselines. Per user direction the section is now REMOVED. The
    # agent has only its current baseline + this set's own
    # accumulated state. select_history_entries() is kept in this
    # module as a pure utility but no longer called.
    parts.append(
        "# Accumulated rcParams so far (this baseline set, this run)\n"
        "```json\n"
        + json.dumps(accumulated_rcparams or {}, indent=2, sort_keys=True)
        + "\n```"
    )

    # Output contract — the only place that prescribes file names and
    # retry semantics. Wording matched to the spec scenarios in
    # figcopy-real-runner-backend/spec.md ("Agent retries rendering
    # internally"; "Refine completion is atomic on .json rename").
    next_n = (
        expected_refine_index
        if expected_refine_index is not None
        else _next_refine_index(workdir)
    )
    parts.append(
        "# Output contract\n"
        f"1. Pick the next refine index: {next_n:03d}.\n"
        f"2. Write the figure code to `refine_{next_n:03d}.py`.\n"
        f"3. Render `refine_{next_n:03d}.png` at dpi=300.\n"
        "4. Inspect the rendered image. If it has errors (Python\n"
        "   exception, clipped text, missing labels, blank canvas),\n"
        "   FIX YOUR CODE and rerun. Retry as many times as needed\n"
        "   within this turn until the image renders successfully.\n"
        f"5. Once the PNG is correct, write the structured outcome to\n"
        f"   `refine_{next_n:03d}.json` with shape:\n"
        "   `{\"rcparams_delta\": {...}, \"review\": \"...\","
        " \"baseline_iters\": [...]}`.\n"
        "6. Use atomic writes for both final files: write to\n"
        "   `<name>.tmp` then `os.rename` to the final name. Do NOT\n"
        "   leave any `.tmp` file behind.\n"
        "7. Do NOT declare the turn complete until BOTH\n"
        f"   `refine_{next_n:03d}.png` and `refine_{next_n:03d}.json`\n"
        "   exist with their final names."
    )

    parts.append(f"# User request\n{user_message}")

    return "\n\n".join(parts) + "\n"


def _next_refine_index(workdir: Path) -> int:
    """Determine the next ``refine_NNN`` number by looking at disk.

    Counts completed ``refine_*.json`` files plus in-flight reservation
    files. The first refine on a fresh workdir is index 1.
    """
    return next_refine_index(workdir)
