# v11 — Orchestrator (loop wiring + stop conditions)

> The Drawer and the Reviewer don't talk to each other directly. The orchestrator
> shuttles artifacts between them and decides when to stop.
>
> v11 changes vs v10: orchestrator stages `v11_aesthetic_library.md` into BOTH the
> drawer's working dir AND the reviewer's audit_view, so both can ground claims in
> the L2 convention library. Anchor preserve list semantics are now class-based
> (within ±10% / same L2 class) rather than exact-match. Drawer brief explicitly
> tells the doer to read the library before iter-0.

---

## Roles

- **Orchestrator (this Claude Code session)** — top-level driver. Calls the Drawer,
  reads the Reviewer's JSON, decides accept / revise, hands the reviewer's
  `focus_themes` AND `anchor.what_is_right` (as a hard preserve list) back to the
  Drawer for the next iter.
- **Drawer (sub-agent or in-session)** — system prompt = `v10_drawer.md`.
  Produces `figure_iter<N>.py`, `img_iter<N>.png`, `notes_iter<N>.md`. Runs its own
  programmatic floor check before handoff. Records anchor measurements at iter 0.
  Refuses to modify properties on the orchestrator-forwarded preserve list.
- **Reviewer (Bash subprocess, fresh context, BOUNDED TOOLS)** — system prompt =
  `v10_reviewer.md`. Reads `reference_clean.png`, `img_iter<N>.png`, and (when
  N > 0) `audit_iter<N-1>.json`. Allowed tools: Read + Bash limited to
  `python -c` for PIL measurement. **Does not read `data.txt`.** **Does not write
  files.** Outputs `audit_iter<N>.json`.

The Reviewer being a *fresh-context Bash subprocess* (not a sub-agent within the doer's
session) is critical — same architecture as v5–v9 — because we need the audit to be
unbiased by the doer's reasoning chain. The bounded-tool change in v10 lets the
reviewer ground pixel-level claims (the v9 trap was confident-but-wrong eyeballed
claims about aspect and spine count).

```bash
ITER=<N>
WORKDIR=/.../claude-code-subagent-v11
PROMPTS=/.../spike-results/prompts
REVIEWER_PROMPT=$(cat $PROMPTS/v11_reviewer.md)

# Stage audit_view: the two PNGs + library + (if N > 0) the prior audit JSON.
mkdir -p $WORKDIR/audit_view_$ITER
cp $WORKDIR/inputs/reference_clean.png $WORKDIR/audit_view_$ITER/
cp $WORKDIR/img_iter$ITER.png $WORKDIR/audit_view_$ITER/
cp $PROMPTS/v11_aesthetic_library.md $WORKDIR/audit_view_$ITER/    # NEW IN V11
if [ $ITER -gt 0 ]; then
  cp $WORKDIR/audit_iter$((ITER-1)).json $WORKDIR/audit_view_$ITER/
fi

# Allow Bash + Read; disallow file modification + subagents.
claude -p --model opus --dangerously-skip-permissions \
  --add-dir $WORKDIR/audit_view_$ITER \
  --allowedTools "Read Bash" \
  --disallowedTools "Edit Write NotebookEdit Agent" \
  --append-system-prompt "$REVIEWER_PROMPT" \
  "Iter: $ITER. Reference: audit_view_$ITER/reference_clean.png. Draft: audit_view_$ITER/img_iter$ITER.png. Library (READ THIS FIRST): audit_view_$ITER/v11_aesthetic_library.md. Prior audit (if iter>0): audit_view_$ITER/audit_iter$((ITER-1)).json. Use the L1/L2/L3 hierarchy: ground every claim in L1 (reference) or L2 (library), never L3 (opinion). For PIL-unreliable properties (spines, gridlines, font weight) per the library, use L2 by default — do NOT mean-of-strip PIL these. Output the JSON object specified by the system prompt and nothing else." \
  > $WORKDIR/audit_iter$ITER.json 2> $WORKDIR/audit_iter$ITER.stderr
```

## Per-iter sequence

```
for N in 0..MAX_ITERS:
    # --- DRAWER ---
    if N == 0:
        drawer_brief = (
            "First iter. No prior reviewer feedback.\n\n"
            "REQUIRED before drafting figure_iter0.py:\n"
            "  1. READ `prompts/v11_aesthetic_library.md` in full. It tells you which\n"
            "     properties are PIL-reliable vs PIL-unreliable, and gives you the L2\n"
            "     class menus (spine color classes, gridline classes, font families,\n"
            "     aspect ratio bands, etc.).\n"
            "  2. Do the iter-0 anchor pass:\n"
            "       - PIL-measure ONLY the L1-reliable properties (aspect, palette\n"
            "         from large filled regions, panel grid from visual count).\n"
            "       - For PIL-unreliable properties (spines, gridlines, font weight),\n"
            "         pick the L2 CLASS by eye against the reference.\n"
            "  3. Write `notes_iter0.md` with `## Anchor measurements` section listing\n"
            "     L1-PIL values and L2-class choices, each justified.\n"
            "These are your first-principles anchors for all subsequent iters."
        )
    else:
        prev = json.loads(audit_iter<N-1>.json)
        anchor_preserve = prev["anchor"]["what_is_right"]
        drawer_brief = (
            f"Prior reviewer audit (verbatim JSON): {prev}\n\n"
            f"--- PRESERVE LIST (read the L1/L2 prefix on each item) ---\n"
            f"{anchor_preserve}\n\n"
            f"Each preserve item is prefixed with [L1] / [L2] / [L1+L2]:\n"
            f"  - [L1] items: keep within ±10% of the measured value (or same exact\n"
            f"    class for categorical properties).\n"
            f"  - [L2] items: keep within the same library class. You have within-class\n"
            f"    freedom to adjust, but do NOT move the property into a different class.\n"
            f"  - [L1+L2] items: strongest preserve. Do not change.\n\n"
            f"Address quality_floor.violation_kinds first; the floor must pass before\n"
            f"focus_themes work. Then address focus_themes in order, EXCEPT do NOT move\n"
            f"any preserved property out of its anchor class. If a focus_theme appears\n"
            f"to require moving a preserved property out of its class, surface the\n"
            f"conflict in notes_iter<N>.md and leave the property in its class.\n\n"
            f"If a focus_theme makes a measurable claim about an L1 property that\n"
            f"contradicts your iter-0 anchor PIL measurements, trust your measurements\n"
            f"and push back via notes. If a focus_theme makes a claim about an L2\n"
            f"property and asks for a different L2 class, consider it — your iter-0\n"
            f"L2 choice was eyeballed and the reviewer's eye may be more reliable —\n"
            f"but never accept a value outside ALL L2 classes (e.g. a #dcdcdc spine)."
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

If the loop exits at `MAX_ITERS` without `ship`, the orchestrator runs a final
PIL-grounded selection pass against `inputs/reference_clean.png`:

1. **Compute drift score per iter:** for each iter, measure `abs(draft.aspect -
   ref.aspect) / ref.aspect` and `abs(draft_visible_spines - ref_visible_spines)`.
   Combine into a "drift distance from reference."
2. Among iters with `quality_floor.passed = true` AND `fidelity.verdict = "close"`,
   pick the one with the **lowest drift distance** (NOT the most recent — most
   recent was the v9 mistake that selected iter5 with 21% aspect drift over iter1
   with 7% aspect drift).
3. Fallback: if no `close` iters, fall through to the v9 rules (any floor-passing,
   then shortest violation list).

Document the choice in `selection.md` with the drift distances tabulated. If the
selected iter is NOT the most recent, that is itself a signal that the loop drifted
and the prompts may need v11 work.

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
