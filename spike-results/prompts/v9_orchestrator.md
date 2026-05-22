# v9 — Orchestrator (loop wiring + stop conditions)

> The Drawer and the Reviewer don't talk to each other directly. The orchestrator
> shuttles artifacts between them and decides when to stop. This document captures the
> wiring; it is the harness, not a system prompt.
>
> Updated to match the leaner reviewer schema (single fidelity verdict + ≤5 themes,
> no scored axes, no per-instance fix_hints, no tools). See `v9_findings.md` §6.

---

## Roles

- **Orchestrator (this Claude Code session)** — top-level driver. Calls the Drawer,
  reads the Reviewer's JSON, decides accept / revise, hands the reviewer's
  `focus_themes` (and floor `summary` if present) back to the Drawer for the next iter.
- **Drawer (sub-agent or in-session)** — system prompt = `v9_drawer.md`.
  Produces `figure_iter<N>.py`, `img_iter<N>.png`, `notes_iter<N>.md`. Runs its own
  programmatic floor check before handoff. **Owns all detail-level work** (data
  fidelity, matplotlib mechanics, pixel measurements). The reviewer doesn't see those.
- **Reviewer (Bash subprocess, fresh context, NO TOOLS)** — system prompt =
  `v9_reviewer.md`. Reads `reference_clean.png` and `img_iter<N>.png`. **Does not read
  `data.txt`.** **Does not run code.** Outputs `audit_iter<N>.json`.

The Reviewer being a *fresh-context Bash subprocess* (not a sub-agent within the doer's
session) is critical — same architecture as v5–v8 — because we need the audit to be
unbiased by the doer's reasoning chain. The new bit in v9 is that the subprocess is
launched **without `--add-dir` to any code/data location**, so the reviewer cannot
cheat by reading the doer's source. Only the two PNGs are exposed.

```
mkdir -p iter<N>/audit_view
cp reference_clean.png img_iter<N>.png iter<N>/audit_view/

claude -p --model opus --dangerously-skip-permissions \
  --add-dir iter<N>/audit_view \
  --no-tools \
  "$(cat v9_reviewer.md)\n\nIter: <N>\nReference: reference_clean.png\nDraft: img_iter<N>.png"
# (--no-tools is the intent flag; if the CLI doesn't expose it,
#  the reviewer prompt itself bans tool use, and the audit_view dir
#  carries only the two PNGs so there's nothing else to read.)
```

## Per-iter sequence

```
for N in 0..MAX_ITERS:
    # --- DRAWER ---
    if N == 0:
        drawer_brief = "First iter. No prior reviewer feedback."
    else:
        prev = json.loads(audit_iter<N-1>.json)
        drawer_brief = (
            f"Prior reviewer audit (verbatim JSON): {prev}\n\n"
            f"Address quality_floor.violation_kinds first; the floor must pass before any\n"
            f"focus_themes work. Then address focus_themes in order. Each focus_theme is\n"
            f"a CATEGORY, not a mechanism — you decide the mechanism. The reviewer\n"
            f"intentionally does not prescribe matplotlib parameters; if you need a\n"
            f"prescription, consult your own NEVER/INSTEAD list."
        )

    invoke drawer with drawer_brief
    # drawer writes figure_iter<N>.py, img_iter<N>.png, notes_iter<N>.md
    # drawer is REQUIRED to have already run its own floor self-check
    # (renderer.get_window_extent bbox-disjoint check, etc.)
    # and to have already verified data fidelity against data.txt

    # --- REVIEWER ---
    invoke reviewer subprocess on iter <N>
    # reviewer writes audit_iter<N>.json

    audit = json.loads(audit_iter<N>.json)

    # --- DECISION ---
    if not audit.quality_floor.passed:
        if N == MAX_ITERS - 1:
            break  # fall through to select-best
        continue   # next iter; drawer must address the floor

    if audit.fidelity.verdict == "off":
        if N == MAX_ITERS - 1:
            break
        continue

    if audit.fidelity.verdict == "close" and N < MAX_ITERS - 1:
        continue   # one more pass

    # fidelity.verdict == "ship" AND floor passed → accept
    chosen_iter = N
    break
```

The decision rule is intentionally smaller than the v5–v8 audit-driven loop: three
inputs (`floor.passed`, `verdict`, budget remaining), one output (continue or stop).
No score arithmetic.

## Hard cap

`MAX_ITERS = 6` (revised from 5 in the first draft of this doc, after the user noted
the loop benefits from one more pass when the doer is responsible for more of the
craft and the reviewer is leaner).

## Select-best fallback (if `ship` never fires)

If the loop exits at `MAX_ITERS` without `ship`, pick:

1. The iter with `quality_floor.passed = true` AND `fidelity.verdict = "close"`
   (most recent wins ties).
2. Else the iter with `quality_floor.passed = true` AND `fidelity.verdict = "off"`
   (better off-style than broken).
3. Else the iter with the **shortest** `quality_floor.violation_kinds` list.

Document the choice in `selection.md`. If we end up at #3 (no floor-passing iter),
flag the run as a regression — that's the failure mode v9 is specifically trying to
prevent.

## Drawer ↔ Reviewer coupling

The Reviewer's `quality_floor.violation_kinds` enum and the Drawer's NEVER list are
deliberately *not the same words*, because they serve different layers:

- Drawer's NEVER list is **prescriptive** — names the matplotlib mechanism that
  causes the defect, since the Drawer has matplotlib in hand.
- Reviewer's enum is **descriptive** — names what the defect looks like to the eye,
  since the Reviewer only has eyes.

The mapping (1:1 in spirit, not in wording):

| Reviewer enum | Drawer NEVER pair |
| --- | --- |
| `text_overlaps_tick` | "NEVER let an annotation text bbox intersect a tick-label text bbox" |
| `text_overlaps_title` | "NEVER let a per-point data label cross a subplot boundary" |
| `text_overlaps_text_in_axes` | (covered by the bbox-disjoint invariant) |
| `label_clipped` | "NEVER let `set_xlabel(...)` clip off the bottom of the canvas" |
| `axis_drawn_off_canvas` | (same family as label_clipped) |
| `illegible_at_print_size` | "NEVER force figsize × dpi == reference_pixel_dimensions" |
| `default_matplotlib_aesthetic` | "NEVER ship default matplotlib spines, default tick directions, or default gridline treatment" |

The Reviewer's `focus_themes` are deliberately **not** mapped to specific NEVER pairs —
themes are pattern-level, and the Drawer translates them into mechanism. This is
intentional: it prevents the reviewer from drifting into prescriptive matplotlib advice
that would (a) couple the loop tightly to the reference's specific geometry and (b)
break in the data-migration case where the right mechanism for OUR data is not the
mechanism that produced the reference's image. (See findings §6.)

## What the orchestrator should NOT do

- Do not score the figure itself; the Reviewer is the only judge.
- Do not summarize the Reviewer's JSON in natural language for the Drawer; pass the
  JSON object verbatim — the Drawer reads the schema.
- Do not translate `focus_themes` into matplotlib mechanisms before handing off — that
  collapses the reviewer/doer separation that v9 is built on.
- Do not mix Stage 1 (style transfer, this loop) with Stage 2 (per-figure tweaks).
  v9 only covers Stage 1.
- Do not feed the data file or any code path into the Reviewer's working dir. Vision
  only.

## Logging artifacts (per iter, in iter dir)

- `figure_iter<N>.py`
- `img_iter<N>.png`
- `notes_iter<N>.md`
- `audit_iter<N>.json`
- `floor_selfcheck_iter<N>.txt` (Drawer's own programmatic floor check before handoff —
  the reviewer's floor pass should always agree with this; if they disagree we have a
  reviewer calibration bug worth investigating)

After loop exit:
- `selection.md` (chosen iter + reasoning)
- `figure.py` / `figure.png` / `figure.pdf` (copies of chosen iter)
- `process.md` (per-iter changelog tying reviewer themes to revisions; consistent with
  v5–v8 conventions so the runs are comparable)
