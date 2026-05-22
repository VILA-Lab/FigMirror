# v5 Prompt — record

This is the prompt used in `spike-results/claude-code-subagent-v5/`.
Key new ideas in v5 vs v4:
- Hard rule R1: NO color substitution (don't borrow palette colors for unsampled elements)
- Hard rule R2: checklist is a floor, not a ceiling — add free-form reflection step
- Hard rule R3: evidence over inference (PIL > visual when possible)
- Module 2 EXTENDED palette: also samples title/label/frame text colors
- Stage C audit via Bash subprocess (`claude -p --model opus --dangerously-skip-permissions`),
  not via Agent tool (which proved unable to nest)
- Stage C select-best: not strict monotonic improvement; pick the best of all iters
  via cross-iter audit comparison

Known remaining bugs found post-spike:
- B1: figsize_inches × dpi DID NOT enforce match to reference pixel dimensions —
  agent used (13.0, 6.8) → (11.5, 7.5) when correct value was (~7.42, ~3.86) at dpi=180
- B2: wspace stayed at 0.07 across all 4 iterations (never tuned); hspace tuned but inconsistently
- B3: legend frame used `boxstyle="Square"` instead of `"Round"` — reference shows rounded corners
- B4: no multi-variant final output; only one figure delivered

The full v5 prompt is reproduced below.

---

## TASK

Reproduce a paper-quality figure that mimics the visual STYLE of a reference image,
using a given dataset. We are imitating STYLE, not duplicating the reference. The
data this script plots is OUR data, different from the reference.

The output figure must NOT include a caption.

## INPUTS / OUTPUTS / ENV

(See spike-results/claude-code-subagent-v5/ for actual paths used.)

Required deliverables: inputs/reference_clean.png, clean_reference.py, extracted.md,
extract_colors.py, measure_layout.py, figure_iter0..N.py, img_iter0..N.png,
audit_iter0..N.md, selection.md, figure.py/pdf/png, process.md.

Headless audit: `claude -p --model opus --dangerously-skip-permissions`
Python: project Python environment with matplotlib, numpy, and PIL installed.

## HARD RULES

R1. NO COLOR SUBSTITUTION. If you didn't actually PIL-sample a color for an element,
    you may NOT borrow a color from the extracted palette. Either sample now, use
    matplotlib default with comment, or mark UNKNOWN.

R2. CHECKLIST IS A FLOOR, NOT A CEILING. After Module 1 checklist, MUST do a
    free-form reflection step listing distinctive items the checklist did not
    naturally capture.

R3. EVIDENCE OVER INFERENCE. PIL measurement preferred to visual inference. Mark
    inferences explicitly.

## STAGE A — Reference cleaning

A1 Survey distractors. A2 Pick target if multiple figures. A3 Compute target bbox.
A4 Write clean_reference.py + run → reference_clean.png. A5 Self-verify (REMOVED set
present, PRESERVED set intact). Cap 3 cleaning attempts.

## STAGE B — Perception

### Module 1 — Element checklist + free-form reflection

Part A: walk through 14-item checklist (subplot structure, spines, gridlines, ticks,
background, lines, markers, per-point annotations, shading, legend, axis labels,
per-panel titles, caption [record only], aspect/compactness). For each: ✓ / ✗ / ?
plus specifics. Pay extra attention to legend frames (PIL-sample border in M2)
and per-panel title color (PIL-sample text pixels in M2).

Part B: free-form reflection — list distinctive features beyond the checklist
categories. If nothing, write "Nothing beyond the checklist" explicitly.

### Module 2 — Color palette (EXTENDED, all PIL-sampled)

Cover at minimum:
(a) per-series accent (line+marker)
(b) baseline (V1) line/marker
(c) annotation text colors (V2 value, V1 value, delta arrow)
(d) shaded fill colors + alpha
(e) spine color
(f) gridline color
(g) **column title text color** ← sample text pixels
(h) y-axis label text color
(i) x-axis label text color
(j) tick label text color
(k) **legend frame BORDER color** ← if near-white, use `frameon=False`/`edgecolor='none'`
(l) legend background fill color

Use PIL median RGB after filtering. UNKNOWN entries must NOT be silently substituted.

### Module 3 — Layout & typography (PIL measurement)

Plot dims; subplot pixel size; column gap (wspace px); row gap (hspace px);
outer margins; tick label height; y-axis title height; column title height;
annotation height; legend text height; V1/V2 line stroke widths; spine width;
gridline width; V1/V2 marker diameters.

Translate at dpi=180:
  figsize_inch = (W_in, H_in) such that figsize × 180 ≈ pixel size
  fontsize_pt = px_height × 72 / 180
  wspace = col_gap_px / subplot_width_px
  hspace = row_gap_px / subplot_height_px
  linewidth_pt, markersize_pt = px × 72 / 180

## STAGE C — Draft + audited compare-revise + select-best

C1 — iter0: build with Module 1 + Module 2 (R1) + Module 3 translation. Inline data,
Type 42 fonts, NO caption.

C2 — compare-revise loop, max 3 inner revisions:
  (a) DOER COMPARE: walk inventory + reflection items; ≥3 color sample regions;
      compactness check by cropping own output's plot body the same way as Stage A.
  (b) FRESH-CONTEXT AUDIT (Bash subprocess, not Agent tool):
      claude -p --model opus --dangerously-skip-permissions --add-dir <dir>
      "$AUDIT_PROMPT" > audit_iter<N>.md
      AUDIT_PROMPT covers: text overlaps, element fidelity (both directions),
      compactness, color fidelity (esp. legend frames + titles), typography,
      other anomalies. Specific locations required.
  (c) Combine doer + audit; revise; save figure_iter<N>.py + img_iter<N>.png.

C3 — SELECT BEST: run audit on every iter; document scores in selection.md;
pick best iter (NOT necessarily highest N) with reasoning; copy chosen
figure_iter<K>.py to figure.py; render figure.pdf and figure.png.

C4 — process.md: per-iter changelog + which audit findings drove which revisions
+ final selection rationale.
