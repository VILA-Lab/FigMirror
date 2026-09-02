---
name: figmirror
description: This FigMirror skill should be used when the user asks to "mirror this figure's style", "copy this figure's style", "make a chart that looks like this paper", "reproduce this figure with my data", "match this paper's aesthetic", "I want a NeurIPS-quality version of this", or any variant where they hand over a cropped or uncropped reference figure AND their own data and want their data rendered in the same visual register. ALSO triggers when the user attaches a paper-figure screenshot plus tabular data and asks for matplotlib output. Does NOT trigger on generic matplotlib chart requests with no reference image — that's a basic matplotlib task, not style transfer.
---

# FigMirror (`figmirror`)

Transfer the visual style of a top-conference paper figure (NeurIPS / ICML /
ICLR / Nature / Science) onto user data via an iterative Drawer / Reviewer
loop. Output is a self-contained matplotlib script + PNG + type-42 PDF that
matches the reference's STYLE — not its data.

## When to use

Trigger when the user provides all three:

- A reference paper-figure screenshot.
- Their own data in any parseable form (pasted table, CSV, TSV, markdown
  table, or dirty terminal text).
- An expectation that the output should look like the reference — even via
  casual phrasing ("make this chart but with my numbers", "redo this in
  matplotlib"). This includes 3D references when the reference or data is
  actually 3D.

Do not trigger on plain matplotlib chart requests with no reference image.

## Required inputs

- Reference image (PNG / JPG), cropped or uncropped. It may include margins,
  captions, neighboring panels, or page text; Stage 0 preprocesses it.
- User data (any parseable form).
- Optional working directory. Default: `<cwd>/figmirror-runs/<run-id>/`.

## 3D Insert Gate

Enable `references/three-d-prompting.md` only when the user asks for a 3D
figure, the reference is visibly 3D, or the parsed data requires a 3D encoding
such as `x/y/z`, surfaces, trajectories, layered profiles, closed objects, 3D
small multiples, 3D bars, or plane projections. Do not use this insert to turn
an ordinary 2D task into 3D.

## Architecture

Three bundled subagents drive the loop; the caller orchestrates from the main
thread:

- `figure-preprocessor` — **Preprocessor**. Stage 0: preserves the raw upload,
  crops away margins/captions/page text/neighboring panels when safe, and writes
  `inputs/reference_clean.png` plus a crop check/report.
- `figure-illustrator` — **Drawer**. Per iter: reads reference + data + L2
  library, produces `figure_iter<N>.py`, `img_iter<N>.png`,
  `notes_iter<N>.md`, `floor_selfcheck_iter<N>.txt`. Self-checks the layout
  floor before returning.
- `figure-critic` — **Reviewer**. Per iter: vision-only audit on a
  fresh-context view (reference + draft + L2 library + optional 3D insert +
  prior audit only). Returns ONE strict JSON object per the review schema.

Subagents are stateless across dispatch; iter-to-iter state flows through
workdir files.

Prefer `subagent_type: figure-preprocessor` / `figure-illustrator` /
`figure-critic`. Fallback path
when those names don't resolve: see `references/iter-loop-spec.md` §
"Subagent dispatch fallback".

## Workflow

For each run:

1. **Stage workdir.** Pre-create every directory the loop will write into
   (subagents Write into existing dirs only — workspace permission quirk).
   Stage the uploaded reference image to `inputs/reference_raw.png` and also
   to `inputs/reference_clean.png` as a temporary first-paint copy; stage parsed
   data to `inputs/data.txt`, and the L2 library to
   `inputs/aesthetic-library.md`; stage the 3D router plus
   `references/three-d/` only when the 3D insert gate is enabled. The router
   selects exactly one mode file: `three-d/style-transfer.md` for ordinary
   user-data figures, or `three-d/strict-reproduction.md` for reproduction,
   comparison, or candidate/control replacement. For strict 3D reproduction runs
   that need quantitative candidate diagnosis, also stage the optional candidate
   scorer. The top-level Orchestrator owns final selection and must run the
   selected mode's rendered-image gates before copying any candidate to the
   final figure.
2. **Preprocess reference.** Dispatch `figure-preprocessor` before data-gen,
   Drawer, or Reviewer. It writes the clean L1 anchor to
   `inputs/reference_clean.png` and records the before/after crop check.
3. **Echo data parse to user (Decision-7).** Show parsed shape (rows × cols,
   columns, NaN cells, sample row); proceed when confirmed, or skip if the
   user pre-authorized. Either way, persist the echo to `data_echo.md`.
4. **Iterate** with the caller-provided `max_iters`; default to 6 when the
   caller gives no explicit limit. If the caller enables auto-until-shipped,
   ignore `max_iters` and continue until `ship` or a real blocker. Each iter:
   - Dispatch the Drawer.
   - Stage the Reviewer's audit view (reference + new draft + L2 library +
     optional 3D insert + prior audit only — NEVER `data.txt` or drawer
     notes).
   - Dispatch the Reviewer.
   - Parse the audit JSON.
   - Apply the decision rule:
     `floor.passed && verdict == "ship"` → ship and break;
     else `N == max_iters - 1` and not auto → break (fall through to select-best);
     else continue.
5. **Select-best fallback** (only if `ship` never fires). Pick the
   lowest-drift iter among `floor.passed && verdict == "close"` candidates.
   Document the choice in `selection.md`.
6. **Write canonical artifacts.** Copy the chosen-iter script + PNG to
   `figure.py` / `figure.png`. Re-render `figure.pdf` with
   `pdf.fonttype = 42`.
7. **Surface the result to the user.** Render `figure.png` inline, list
   paths to `figure.py` / `figure.pdf`, give a 1-2 sentence trajectory
   summary. Do not show audit JSONs or per-iter scripts unless asked.

The full per-step spec (bash commands for staging, dispatch brief
templates, audit JSON parsing snippets, drift calculation, fallback
selection) lives in `references/iter-loop-spec.md`. Read it before
running the loop.

## Non-negotiables

- The reference is a **STYLE anchor, not a layout-number anchor**. The
  Drawer must NOT copy `wspace`, `hspace`, `figsize`, `ylim` from the
  reference's data — those recompute from OUR data's shape.
- `inputs/reference_raw.png` is the preserved upload; `inputs/reference_clean.png`
  is the Stage-0 crop used for L1 measurement.
- Every visual choice traces to **L1** (reference image) or **L2**
  (`references/aesthetic-library.md`). **L3** ("I think it would look
  better") is banned.
- The Reviewer's audit view contains ONLY reference + draft + L2 library +
  optional 3D insert + prior audit. NEVER stage `data.txt` or drawer notes
  into it. Vision-only audit preserves reviewer independence.
- Pre-create all directories before dispatching subagents; subagents only
  Write into existing dirs.
- Iter N>0 Drawer must edit the prior iter's `.py` incrementally (copy →
  edit copy), not rewrite from scratch. All prior iters' artifacts must
  remain intact in workdir.
- Final script is self-contained (inline DATA SECTOR) with
  `matplotlib.rcParams['pdf.fonttype'] = 42`.

## Bundled resources

- `references/aesthetic-library.md` — L2 convention library (~900 lines).
  Read by both Drawer and Reviewer per iter. Versioned independently of the
  agent prompts because the library iterates faster.
- `references/three-d-prompting.md` — conditional 3D L2 insert router. Stage
  and pass it only when the reference is visibly 3D or the data requires 3D
  encoding.
- `references/three-d/` — 3D mode files plus routed modules for core gates,
  surfaces, marks/panels, strict scorecards, and repair feedback.
- `scripts/score_3d_candidates.py` — optional 3D strict-reproduction helper for
  diagnosing rendered camera/aspect/layout candidates against the L1 reference.
- `references/iter-loop-spec.md` — full per-step orchestration spec
  (staging commands, dispatch briefs, JSON parsing, drift calc, fallback).

## Bundled subagents

- `figure-preprocessor` — Stage-0 reference crop role.
- `figure-illustrator` — Drawer role.
- `figure-critic` — Reviewer role.

Source: `.claude/agents/figure-{preprocessor,illustrator,critic}.md`
(project-level) or `~/.claude/agents/figure-{preprocessor,illustrator,critic}.md`
(user-level after
`scripts/install_claude_skill.py`).
