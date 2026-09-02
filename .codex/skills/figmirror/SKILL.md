---
name: figmirror
description: >
  FigMirror mirrors the visual style of a top-conference paper figure (NeurIPS /
  ICML / ICLR / Nature family) onto the user's own data. Takes dirty data plus a
  reference figure screenshot (cropped or uncropped), preprocesses the reference
  crop, runs a Drawer/Reviewer loop, and outputs a camera-ready PDF plus a
  self-contained matplotlib script with an inline DATA SECTOR.
---

# FigMirror (`figmirror`)

Use this skill when the user wants to:
- Transfer the visual style of a top-conference paper figure to their own data.
- Produce a camera-ready matplotlib figure matching a reference screenshot in
  style, not in data.
- Mirror 3D paper-figure references such as surfaces, scatter, trajectories,
  bars, layered waterfalls, or plane projections when the reference or data is
  actually 3D.
- Receive a self-contained `.py` script with editable inline data plus PNG/PDF
  outputs.

## Required Inputs

- A reference figure screenshot (`PNG`/`JPG`). It may include margins, captions,
  neighboring panels, or page text; Stage 0 preprocesses it.
- The user's data in any parseable form: pasted table, CSV, TSV, markdown table, or
  dirty terminal text.
- A working directory for iteration artifacts.

## 3D Insert Gate

Enable `references/three-d-prompting.md` only when the user asks for a 3D
figure, the reference is visibly 3D, or the parsed data requires a 3D encoding
such as `x/y/z`, surfaces, trajectories, layered profiles, closed objects, 3D
small multiples, 3D bars, or plane projections. Do not use this insert to turn
an ordinary 2D task into 3D.

## Architecture

- **Python runner** owns UI lifecycle, cancellation, Stage-0 bootstrap, optional
  data-gen, and launching the main Codex process.
- **The top-level Codex process is Orchestrator only.** It owns iteration state,
  role dispatch, artifact checks, Reviewer audit-view staging, deterministic
  review-gate invocation, stop decisions, and final selection.
- **Drawer** runs as the named `figmirror-drawer` custom subagent through
  `spawn_agent` with `fork_context=false`. It writes each iteration's matplotlib
  script, render, notes, and floor self-check in the staged workdir.
- **Reviewer** runs as the named `figmirror-reviewer` custom subagent through
  `spawn_agent` with `fork_context=false`. It sees only the staged audit view:
  the far-view composite, full-resolution reference/draft near views, the
  Reviewer prompt, the aesthetic library, fixed diagnostics, and optional fixed
  3D audit material. It returns strict
  JSON including `boxes`; `figannot.py review-decision` validates and records the
  result before any Drawer or finalization decision. The Orchestrator sends the task text and visual
  bundle together in one structured `items` payload. The Reviewer must use
  those attached pixels directly: Do not call `view_image` or reopen image
  paths.
- **3D flow** uses the standard Orchestrator plus named Drawer/Reviewer
  subagents, and optional candidate-scoring path for strict reproduction.

## Workflow

1. Read these bundled references from this skill directory:
   - `references/preprocessor.md` for Stage-0 reference crop cleanup.
   - `references/orchestrator-codex.md` for loop wiring and stop conditions.
   - `references/drawer.md` for the Drawer instructions.
   - `references/reviewer.md` for the Reviewer instructions.
   - `references/aesthetic-library.md` for the L2 convention library.
   - `references/three-d-prompting.md` only when the 3D insert gate is enabled.
2. Preserve the uploaded reference as `inputs/reference_raw.png`, then run the
   reference preprocessor to write `inputs/reference_clean.png`,
   `inputs/reference_crop_check.png`, and `inputs/reference_crop_report.md`.
3. Echo the parsed data structure before drawing. If the user explicitly asked you
   to make up data or proceed without confirmation, record that in `data_echo.md`
   and continue; otherwise ask for confirmation.
4. When the 3D insert gate is enabled, stage `references/three-d-prompting.md`
   plus `references/three-d/` beside the normal prompts. The router selects
   exactly one mode file: `three-d/style-transfer.md` for ordinary user-data
   figures, or `three-d/strict-reproduction.md` for reproduction, comparison, or
   candidate/control replacement. For strict 3D reproduction runs that need
   quantitative candidate diagnosis, also stage `scripts/score_3d_candidates.py`;
   do not use that scorer for ordinary style transfer. The top-level
   Orchestrator owns final selection and must run the selected mode's
   rendered-image gates before copying any candidate to the final figure.
   Always stage `scripts/figannot.py`; it is the deterministic operator for
   building audit composites and drawing Reviewer boxes.
5. In Codex, the top-level agent follows `references/orchestrator-codex.md` and
   spawns `figmirror-drawer` for each iter. Every Drawer prompt includes the
   exact trace line `Iter: <N>` with the current non-negative decimal iteration.
   The Drawer writes
   `figure_iter<N>.py`, `img_iter<N>.png`, `notes_iter<N>.md`, and
   `floor_selfcheck_iter<N>.txt`; the Orchestrator verifies those files before
   any Reviewer handoff.
6. Stage `audit_view_<N>`, run `scripts/figannot.py compose` to create
   `composite.png` and `review_prompt.txt`, and spawn `figmirror-reviewer` as
   described in `references/orchestrator-codex.md`. Attach `composite.png`,
   `reference_clean.png`, and `draft_fullres.png` exactly once as structured
   local-image items; attach the optional strict-3D accepted control once when
   present. The Reviewer sees only those images plus the aesthetic library,
   fixed diagnostics, and optional 3D insert, then returns strict JSON without
   reopening image paths or reading review/Drawer history.
7. Run `scripts/figannot.py review-decision` after every Reviewer result. A
   clean result before the run reaches `min_reviews` total valid Reviewer calls
   starts another Reviewer on the same immutable draft with no Drawer in between.
   Actionable feedback permits `scripts/figannot.py draw --max-iters <max_iters>`
   and another Drawer when below the hard `max_iters` cap. The helper must
   succeed before spawning that Drawer. A missing, malformed, empty, or inconsistent
   Reviewer result returns `retry_reviewer`: it does not count, never triggers
   Drawer, and starts one fresh Reviewer on the same immutable draft. A second
   consecutive invalid result fails closed. Pass both `--min-reviews` and
   `--max-iters` to every `review-decision` invocation, plus `--strict-3d` when
   the router selected `three-d/strict-reproduction.md`.
8. Stop when the deterministic gate returns `ship`: the quality floor passed,
   the verdict is clean, and at least `min_reviews` valid Reviewer calls have
   completed across the run. Default `max_iters=5`; it is always a hard Drawer
   cap. If the gate returns `stop_at_cap`, do not draw again: select under the
   existing hard-cap policy and finalize an existing iteration.
9. Write final `figure.py`, `figure.png`, `figure.pdf`, `output.png`,
   `floor_selfcheck_final.txt`, `selection.md`, `process.md`, and `status.json`.
   `output.png` is the evaluator-facing PNG and may be identical to
   `figure.png`.

## Artifact Layout

```text
<workdir>/
  inputs/
    reference_raw.png
    reference_clean.png
    reference_crop_check.png
    reference_crop_report.md
    data.txt
    aesthetic-library.md
  prompts/
    preprocessor.md
    drawer.md
    reviewer.md
    orchestrator-codex.md
    aesthetic-library.md
    three-d-prompting.md  # router, only for 3D runs
    three-d/              # mode files and routed 3D modules, only for 3D runs
  tools/
    figannot.py
    score_3d_candidates.py  # optional for strict 3D candidate diagnosis
  figure_iter0.py
  img_iter0.png
  notes_iter0.md
  floor_selfcheck_iter0.txt
  review_attempts/
    attempt_000.json
    attempt_001.json
  audit_view_0/
    reference_clean.png
    draft_fullres.png
    composite.png
    composite_meta.json
    review_prompt.txt
    aesthetic-library.md
    three-d-prompting.md  # router, only for 3D runs
    three-d/              # mode files and routed 3D modules, only for 3D runs
  review_feedback_0/
    review.json
    annotated.png
    notes.md
  audit_iter0.json
  audit_iter0.stderr
  ...
  figure.py
  figure.png
  figure.pdf
  output.png
  floor_selfcheck_final.txt
  selection.md
  process.md
  status.json
```

## Non-Negotiables

- The reference is a style anchor, not a layout-number anchor — but the chart type
  and signature motifs ARE style, not layout numbers. Reproduce them.
- Preserve the source's signature visual motifs — chart type, colorbars, shaded/error
  bands, error bars, streamline fields, stacked/offset construction, insets. Dropping
  or flattening one is a fidelity failure, not a simplification. Only the data values
  and labels change to match `data.txt`.
- `inputs/reference_raw.png` is the preserved upload; `inputs/reference_clean.png`
  is the Stage-0 crop used for L1 measurement.
- Every visual choice must be grounded in L1 (reference image) or L2
  (`references/aesthetic-library.md`); L3 opinion is disallowed.
- Do not modify a property on the Reviewer preserve list outside its L1/L2 class.
- Do not expose `data.txt` or source code to the Reviewer audit view.
- Keep the final script self-contained and set `plt.rcParams["pdf.fonttype"] = 42`.
