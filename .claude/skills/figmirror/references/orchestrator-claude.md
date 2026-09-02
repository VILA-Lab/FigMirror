# Claude Code Orchestrator Wiring

This reference is the Claude-Code-only loop harness for `figmirror`. It is a
port of `orchestrator-codex.md`: the algorithm, the decision state machine, the
iteration budget, and every fail-closed rule are unchanged. Only the dispatch
mechanism differs.
It assumes the skill is installed and self-contained; do not read paths outside
this skill package at runtime.

Claude Code runtime shape: the top-level `claude` process is Orchestrator only.
It owns staging, iteration state, role prompts, render verification, Reviewer
audit-view construction, subagent dispatch through the `Task` tool, the next
action after each deterministic decision, selection, and finalization. It
delegates drawing to the named `figmirror-drawer` subagent and visual review to
the named `figmirror-reviewer` subagent using `Task` with
`subagent_type` set to that exact name; generic `general-purpose` / `Explore` /
`Plan` roles are not valid substitutes **for these two roles**. An unknown
`subagent_type` is a hard error naming the available agents — treat that error
as a fatal configuration fault and stop the sample; never fall back to a generic
role for drawing or review. The Stage-0 preprocessor is the one exception: it
has no named role here or on Codex, and runs as a general-purpose subagent. Candidate-pool
generation is an optional host-level mode and is outside the default shipped
loop.

Every `Task` call in this loop MUST pass `run_in_background = false`.
Background dispatch silently removes tools from the child's tool surface and
turns the call into a launch stub instead of a result, which breaks both the
Drawer's render step and the strict draw-then-review ordering below.

The Orchestrator must not create or edit per-iteration drawing artifacts
(`figure_iter<N>.py`, `img_iter<N>.png`, `notes_iter<N>.md`, or
`floor_selfcheck_iter<N>.txt`) itself. Those files are Drawer-owned protocol
outputs. A synchronous `Task` call returns only when the child reaches a
terminal state, so there is no waiting protocol to run and no way to observe a
half-finished Drawer: either the call returns and the bundle is checked, or the
whole run hits the host-level wall clock. Do not attempt to interrupt a running
Drawer with `TaskStop`; `TaskStop` is not part of this loop and must not appear
in the Orchestrator's tool list. Only a returned `Task` whose four-file bundle
is still incomplete may trigger a re-dispatch of the same `figmirror-drawer` role
with a narrower repair task; do not draw inline.

Dispatch strictly one subagent at a time. Draw, review, and decide are
sequential: the round-`N` Reviewer `Task` may only be issued after the round-`N`
Drawer `Task` has returned and its bundle has been verified. Never issue two
`Task` calls in one assistant turn.

The Orchestrator must also not perform visual/style judgment itself, even as a
"sanity look" at `img_iter<N>.png` or `composite.png`. Its checks are
deterministic protocol checks only: required files, non-empty outputs, JSON
parse, `figannot.py` compose/draw success, and final-bundle existence. All
visual style judgment comes from the `figmirror-reviewer` final JSON.

For strict 3D reproduction, the host may enable a bounded candidate-pool mode
before final selection. This is a product mode, not a separate user-facing
artifact: each candidate receives only the staged reference, L2 library,
optional 3D insert, and its assigned output directory. Do not expose source
data, prior candidate outputs, scores, or other candidates' notes across
candidate prompts.

## Setup

Resolve paths at the start of a run:

If `FIGMIRROR_PYTHON_CMD` is unset, first verify `UV_CACHE_DIR`. When it is
unset or points under `$HOME` / the root filesystem, run `df -h`, choose the
largest non-root writable filesystem, create a user-owned FigMirror cache
directory there, and export `UV_CACHE_DIR` to it. Stop with setup guidance if no
safe external cache is available.

```bash
WORKDIR=/absolute/path/to/run-directory
SKILL_DIR=/absolute/path/to/figmirror
REFERENCES=$SKILL_DIR/references
USE_3D_INSERT=${USE_3D_INSERT:-0}
USE_3D_CANDIDATE_SCORER=${USE_3D_CANDIDATE_SCORER:-0}
if [ -n "${FIGMIRROR_PYTHON_CMD:-}" ]; then
  PYTHON_CMD=$FIGMIRROR_PYTHON_CMD
elif command -v uv >/dev/null 2>&1 && [ -n "${UV_CACHE_DIR:-}" ]; then
  PYTHON_CMD="uv run --with pillow --with matplotlib --with numpy python"
else
  echo "FigMirror needs uv and UV_CACHE_DIR on a non-root writable filesystem; configure both and retry." >&2
  exit 2
fi
```

Use `PYTHON_CMD` for every Python invocation in this workflow, including
`tools/figannot.py` help/compose/check-drawer-bundle/review-decision/draw, Drawer render checks, and final
bundle execution. Bare `python` / `python3` commands are not valid in this repo.
Do not run Python just to summarize `inputs/data.txt` when `data_echo.md` is
already present; read the staged summary and inspect `inputs/data.txt` directly
only for semantic details needed by the Drawer brief.

Stage the local run copy of the bundled references:

```bash
mkdir -p "$WORKDIR/inputs" "$WORKDIR/prompts" "$WORKDIR/tools"
cp "$REFERENCES/drawer.md" "$WORKDIR/prompts/drawer.md"
cp "$REFERENCES/preprocessor.md" "$WORKDIR/prompts/preprocessor.md"
cp "$REFERENCES/reviewer.md" "$WORKDIR/prompts/reviewer.md"
cp "$REFERENCES/orchestrator-claude.md" "$WORKDIR/prompts/orchestrator-claude.md"
cp "$REFERENCES/aesthetic-library.md" "$WORKDIR/prompts/aesthetic-library.md"
cp "$REFERENCES/aesthetic-library.md" "$WORKDIR/inputs/aesthetic-library.md"
cp "$SKILL_DIR/scripts/figannot.py" "$WORKDIR/tools/figannot.py"
cp "$SKILL_DIR/scripts/fit_images.py" "$WORKDIR/tools/fit_images.py"
```

Set `USE_3D_INSERT=1` only when the user asks for a 3D figure, the reference is
visibly 3D, or the parsed data requires 3D encoding. When enabled, stage the
conditional router, mode files, and 3D modules:

```bash
if [ "$USE_3D_INSERT" = "1" ]; then
  mkdir -p "$WORKDIR/prompts/three-d" "$WORKDIR/inputs/three-d"
  cp "$REFERENCES/three-d-prompting.md" "$WORKDIR/prompts/three-d-prompting.md"
  cp "$REFERENCES/three-d-prompting.md" "$WORKDIR/inputs/three-d-prompting.md"
  cp "$REFERENCES"/three-d/*.md "$WORKDIR/prompts/three-d/"
  cp "$REFERENCES"/three-d/*.md "$WORKDIR/inputs/three-d/"
fi
if [ "$USE_3D_CANDIDATE_SCORER" = "1" ]; then
  test "$USE_3D_INSERT" = "1"
  cp "$SKILL_DIR/scripts/score_3d_candidates.py" "$WORKDIR/tools/score_3d_candidates.py"
fi
```

The uploaded reference image must be preserved as
`$WORKDIR/inputs/reference_raw.png`. For first paint or older workdirs,
`$WORKDIR/inputs/reference_clean.png` may initially be a copy of the upload; Stage
0 overwrites it with the cleaned crop. The parsed or original data must be stored
as `$WORKDIR/inputs/data.txt`.

## What the runner must provide

The following values come from the caller or the staged workdir. Use the listed
fallback only where one is defined.

| Value | Delivered as | Missing behaviour |
|---|---|---|
| `WORKDIR` | absolute path in the task prompt | stop |
| `SKILL_DIR` | absolute path in the task prompt | stop |
| `FIGMIRROR_PYTHON_CMD` | environment variable | use the isolated `uv run --with ... python` fallback from Setup |
| `max_iters` | task prompt, integer | default 5 |
| `min_reviews` | task prompt, integer | default 2 |
| `inputs/reference_raw.png`, `inputs/data.txt` | staged on disk before dispatch | stop |

The Web UI runner injects a project-pinned command through
`FIGMIRROR_PYTHON_CMD`. A direct skill-only invocation has no repository path to
pin, so the fallback asks `uv` for Pillow, matplotlib, and NumPy explicitly and
requires a preselected external `UV_CACHE_DIR`. If the injected command, `uv`,
or the external cache is unavailable, stop with the setup message from the
Setup block.

## Stage 0: Reference Preprocessing

Before data generation, Drawer, or Reviewer, run the reference preprocessor as a
separate bounded agent using `prompts/preprocessor.md`. Dispatch it with `Task`
and `run_in_background = false`, as a general-purpose subagent — there is no
named preprocessor role, on this harness or on Codex, and the
named-role requirement above applies only to Drawer and Reviewer. Give it the
absolute paths it needs and nothing else; it is a bounded one-shot pass, not a
participant in the loop.

If the runner has already produced `inputs/reference_clean.png` and
`inputs/reference_crop_report.md` before the loop starts, skip this stage rather
than redoing it. What is never acceptable is silently proceeding with
`reference_clean.png` still a copy of the raw upload: every later round measures
against it, so an unprocessed anchor degrades the whole run without any signal. It must read
`inputs/reference_raw.png`, crop away removable whitespace/captions/page text or
neighboring panels, compare the before/after crop, and write:

- `inputs/reference_clean.png`
- `inputs/reference_crop_check.png`
- `inputs/reference_crop_report.md`

If the crop would remove figure information, retry with a larger box. If no safe
crop exists, preserve the raw image as `reference_clean.png` and record `no safe
crop` in the report.

## Per-Iteration Loop

Use the `max_iters` and `min_reviews` values provided by the caller/runner. If
no values are provided, default to `max_iters = 5` Drawer rounds and
`min_reviews = 2` valid Reviewer calls in total across all drafts. `N` counts
Drawer rounds only; repeated review of one immutable draft does not advance
`N`.
Iterate Drawer rounds as `N = 0..max_iters-1`.
`max_iters` is a hard Drawer cap. No auto mode, prompt instruction, or runtime
flag may create Drawer round `max_iters` or later:

1. Orchestrator dispatches `Task` with `subagent_type = "figmirror-drawer"` and
   `run_in_background = false`. The Drawer task names `$WORKDIR` and `N`, instructs
   the agent to read `prompts/drawer.md`, `prompts/aesthetic-library.md`,
   optional `prompts/three-d-prompting.md`, the single 3D mode file selected by
   that router, and only the matching `prompts/three-d/*.md` modules; optional
   `tools/score_3d_candidates.py` when quantitative 3D candidate diagnosis is
   enabled, `inputs/reference_clean.png`, `inputs/reference_crop_report.md` if
   present, `inputs/data.txt`, prior notes, prior audit, and prior annotated
   feedback (`review_feedback_<N-1>/annotated.png` plus
   `review_feedback_<N-1>/notes.md`) if `N > 0`.
2. Drawer writes `figure_iter<N>.py`, `img_iter<N>.png`, `notes_iter<N>.md`,
   and `floor_selfcheck_iter<N>.txt` in `$WORKDIR`. It must not launch `codex`,
   `claude`, or another model process.
   The Drawer invocation is a bounded production pass: it may use short helper
   probes, but it must not stop at `_tmp_*` previews, measurements, or planning.
   Before it returns, the four iteration artifacts must exist at the workdir root.
3. Orchestrator verifies the four iter artifacts are non-empty before any
   Reviewer handoff. The `Task` call is synchronous, so it returns only when the
   Drawer has reached a terminal state; there is no partial-progress state to
   wait through and no patience window to observe. Re-dispatch the same Drawer
   role with a sharper repair task only when the returned bundle is still
   incomplete. Prove that condition by running
   `<PYTHON_CMD> /absolute/path/to/run-directory/tools/figannot.py
   check-drawer-bundle --workdir /absolute/path/to/run-directory --iter N`
   after the `Task` returns and before any replacement dispatch. The
   `PYTHON_CMD` prefix is required: the staged script is copied with mode 0644,
   so running it directly gives a permission error, and its shebang resolves to
   an interpreter without Pillow. Substitute the actual absolute paths in
   this trace-critical command; do not pass `$WORKDIR`, `$FIGANNOT`, or relative path tokens.
   A nonzero result naming at least one missing bundle file is the
   only authorization for one narrower same-iteration replacement; do not draw
   inline as Orchestrator.
4. Orchestrator stages `audit_view_<N>`, builds `composite.png` with
   `tools/figannot.py compose`, fits the staged near views to the delivery limit
   (never `composite.png`; see "Image fitting" below), and dispatches `Task` with
   `subagent_type = "figmirror-reviewer"` and `run_in_background = false`. The
   Reviewer task text carries an explicit ordered list of absolute image paths
   that the Reviewer must open with `Read`, once each, in that order. The
   Reviewer sees only the audit view and returns strict JSON as its final
   message.
5. Orchestrator saves the Reviewer final JSON to a temporary incoming file
   outside the audit view. The incoming filename must contain the exact Reviewer session ID,
   for example `.review_incoming_rev-iter<N>-slot<K>.json`. It MUST NOT interpret
   the fields or write a canonical audit itself. Run `tools/figannot.py review-decision` with the incoming JSON,
   `$WORKDIR`, `N`, `img_iter<N>.png`, the fresh Reviewer child session ID, and
   `min_reviews`, and `max_iters`. The helper performs file validation and the
   atomic decision-state transition only: it never launches a subagent or
   chooses an unreturned next action. For a valid result it records the attempt
   and returns exactly one action for the top-level Orchestrator to follow.
   Missing, malformed, non-object, or internally inconsistent Reviewer output is
   recorded as an invalid terminal attempt and does not count. The first such
   result returns `retry_reviewer`; a second consecutive invalid result is
   persisted and exits nonzero.
   When the 3D router selected `three-d/strict-reproduction.md`, append
   `--strict-3d` to every `review-decision` invocation. Omit it for ordinary
   style transfer. The mode is recorded in the review ledger and cannot change
   mid-run.
   The valid actions are:
   - `retry_reviewer`: the result is invalid and does not count. Retry this
     review slot once with a fresh Reviewer on the same immutable draft. Do not
     run Drawer or expose the invalid payload to the replacement Reviewer. A
     second consecutive invalid result fails closed.
   - `review_same_draft`: the result is a clean `ship`, but fewer than
     `min_reviews` valid Reviewer calls have completed across the run. Dispatch
     a new Reviewer on the same audit view and byte-identical
     `img_iter<N>.png`. Do not dispatch Drawer, do not advance `N`, and do not
     expose the first review JSON to the new Reviewer.
   - `draw`: actionable feedback exists. The helper has written the canonical
     `review_feedback_<N>/review.json` and `audit_iter<N>.json`; run
     `tools/figannot.py draw --max-iters <max_iters>`, then start Drawer round
     `N+1` only if the helper succeeds. The helper returns
     this action only when `N+1 < max_iters`.
     The next valid review must name `N+1` and its canonical draft hash must
     differ from `img_iter<N>.png`; reviewing the old draft again is invalid.
   - `ship`: the clean `ship` meets `min_reviews`. The helper has written the
     canonical audit; select this iteration and finalize without another Drawer.
   - `stop_at_cap`: actionable feedback exists but the current draft is
     `N = max_iters-1` (the fifth draft under the default). Do not run `draw` and
     do not dispatch Drawer. Enter the hard-cap finalization policy below.
6. Only the helper's `draw` action may start another Drawer. Never ask Drawer to
   act on an empty repair brief, an invalid review, or a clean provisional ship.
7. `min_reviews` counts every valid Reviewer call across drafts, whether clean or
   actionable. Invalid results do not count. A clean result before the total
   reaches `min_reviews` reviews the same byte-identical draft again without a
   Drawer call. Actionable feedback starts another Drawer whenever the hard cap
   has not been reached; it does not reset the valid-review count.

The Drawer cap does not prevent additional review calls on the final draft. A
clean result below `min_reviews` still returns `review_same_draft`, and a later
clean result may return `ship`; only another Drawer is prohibited at the cap.

At the hard cap, select the best floor-passing `close` iteration with the
lowest reference drift. If no floor-passing `close` iteration exists, fail the
run instead of promoting an `off` or floor-failing draft.

## 3D Meta Review Gate

When the 3D insert is staged, the Orchestrator acts as the process-level Meta
Reviewer. It does not replace the named visual Reviewer; it checks whether the
Drawer/Reviewer loop is coherent before accepting a repair, selecting a final
render, or declaring `ship`.

For strict 3D reproduction, reject the iteration as meta-invalid and continue or
rerender a narrower repair when any of these process checks fail:

- Reviewer JSON is invalid or lacks `three_d_scorecard`.
- Strict 3D scorecard uses non-canonical field names instead of
  `camera_box_aspect` and `text_export_floor`.
- Reviewer focus does not address the lowest one or two primary 3D dimensions
  before polish.
- Drawer ignores those dimensions without a compact conflict ledger grounded in
  L1/L2 evidence.
- `N > 0` strict repair lacks a rendered accepted-control comparison or changes
  multiple primary registers without separate probes.
- A repair improves color, detail, labels, or cleanliness while regressing
  topology, projected footprint, camera/box aspect, composition/occupancy, or
  export floor.
- A topology or footprint repair changes camera, box aspect, final-export crop,
  subject occupancy, mark overlays, palette semantics, or export floor without a
  separate accepted registration probe.
- A later render is selected merely because it is later.
- `ship` is claimed below the score thresholds, with an active hard gate, or
  with an export-floor failure.

Before adding any new 3D rule to a run-local repair brief, apply a
generalization gate: the rule must be triggered by visible L1 evidence, apply
beyond one example or be explicitly scoped to a visible 3D class, avoid numeric
or construction checkboxes that can be satisfied mechanically, and avoid case
names, paths, prior scores, or run provenance.

## Drawer Execution

Dispatch the Drawer as a named subagent:

```text
Task(
  subagent_type = "figmirror-drawer",
  run_in_background = false,
  description = "figmirror drawer iter $ITER",
  prompt = <the Drawer task text>
)
```

The Drawer task text must begin with these two lines verbatim:

```text
Role: figmirror-drawer
Iter: $ITER
```

The Drawer prompt must be self-contained and name the working directory, iter
index, staged prompt paths, input paths, prior audit path when present, and the
four required output files. It must also name the local render command from
`PYTHON_CMD`; the Drawer must use that command instead of guessing `python` or
`python3`.
Put the exact lines `Role: figmirror-drawer` and `Iter: $ITER` near the top of
the prompt, replacing `$ITER` with the current non-negative decimal iteration,
so the transport trace can be deterministically audited.
State that the task is a bounded production pass: temporary probes are allowed
only as local aids, and the Drawer must write `figure_iter<N>.py`,
`img_iter<N>.png`, `notes_iter<N>.md`, and `floor_selfcheck_iter<N>.txt` before
returning.

For `N = 0`, perform the Drawer prompt's anchor-measurement pass before writing
the first figure. For `N > 0`, copy `figure_iter<N-1>.py` to
`figure_iter<N>.py` and edit incrementally. For strict 3D, source that copy from
the current accepted iteration when it differs from `N-1`. Respect
`audit_iter<N-1>.json.anchor.what_is_right` as a preserve list, address
`quality_floor.violation_kinds` before fidelity themes, and explain any conflict
between Reviewer feedback and measured anchors in `notes_iter<N>.md`. If there
is a real conflict, the notes must include a compact `## Conflict ledger` section
so the next Reviewer can spend extra effort on that property.

For `N > 0`, the Drawer prompt must also name the prior annotated feedback:
`review_feedback_<N-1>/annotated.png` and
`review_feedback_<N-1>/notes.md`. The annotated
image is the far-view reference|draft composite with numbered boxes on the
draft side; `notes.md` maps each number to the actionable mismatch. The Drawer
should fix those boxed spots first, then preserve any prior
`anchor.what_is_right` entries unless L1/L2 evidence proves a correction.

For strict 3D repairs with `N > 0`, keep a rendered accepted-control candidate
under final export settings and compare it against each probe before Reviewer
handoff. If every probe regresses topology, footprint, camera/aspect,
composition/occupancy, or export floor, export the accepted control as the
iteration result and mark the repair unresolved in notes.

Before launching the Reviewer, verify that `figure_iter<N>.py`,
`img_iter<N>.png`, `notes_iter<N>.md`, and `floor_selfcheck_iter<N>.txt` exist
and are non-empty. The `Task` call is synchronous, so control returns only after the Drawer has
finished; a returned call with a missing artifact is real evidence, not an
early peek. Repair missing artifacts by re-dispatching `figmirror-drawer` with
the same role and a narrower repair instruction. Do not use `TaskStop` anywhere
in this loop. Run the incomplete-bundle check with the actual absolute script
and workdir paths, not shell variables or relative path tokens.

## Reviewer Invocation

```bash
ITER=<N>
FIGANNOT="$WORKDIR/tools/figannot.py"
AV="$WORKDIR/audit_view_$ITER"
test ! -e "$AV"
mkdir "$AV"
cp "$WORKDIR/inputs/reference_clean.png" "$AV/reference_clean.png"
cp "$WORKDIR/img_iter$ITER.png" "$AV/draft_fullres.png"
cp "$REFERENCES/reviewer.md" "$WORKDIR/audit_view_$ITER/reviewer.md"
cp "$REFERENCES/aesthetic-library.md" "$WORKDIR/audit_view_$ITER/aesthetic-library.md"
if [ -f "$WORKDIR/prompts/three-d-prompting.md" ]; then
  cp "$WORKDIR/prompts/three-d-prompting.md" "$WORKDIR/audit_view_$ITER/three-d-prompting.md"
  if [ -d "$WORKDIR/prompts/three-d" ]; then
    mkdir -p "$WORKDIR/audit_view_$ITER/three-d"
    cp "$WORKDIR"/prompts/three-d/*.md "$WORKDIR/audit_view_$ITER/three-d/"
  fi
  if [ "$ITER" -gt 0 ] && [ -n "${ACCEPTED_ITER:-}" ]; then
    cp "$WORKDIR/img_iter$ACCEPTED_ITER.png" "$WORKDIR/audit_view_$ITER/accepted_control.png"
  fi
fi
bash -lc "$PYTHON_CMD \"$FIGANNOT\" compose \
  --ref \"$WORKDIR/inputs/reference_clean.png\" \
  --draft \"$WORKDIR/img_iter$ITER.png\" \
  --reviewer-md \"$REFERENCES/reviewer.md\" \
  --out-dir \"$AV\""
```

### Image fitting

Claude Code downscales any image whose long edge exceeds 2000 px before the
model sees it, and reports the applied factor to the reader. Fit the staged
images yourself so that what the Reviewer sees equals what the run recorded,
instead of leaving it to a host-side transform:

```bash
bash -lc "$PYTHON_CMD \"$WORKDIR/tools/fit_images.py\" --max-edge 2000 \
  --report \"$WORKDIR/image_fit_$ITER.json\" \
  \"$AV/reference_clean.png\" \"$AV/draft_fullres.png\" \
  \"$AV/accepted_control.png\""
```

`PYTHON_CMD` must carry its `--project` argument here as everywhere else. A
bare `uv run python` resolves against the current directory, which is `$WORKDIR`
and outside the repository; Pillow is then missing, and both `fit_images.py` and
`figannot.py compose` fail on the import.

The report goes to `$WORKDIR`, not into `$AV`. Anything staged inside the audit
view is admissible evidence for the Reviewer, and delivered-size metadata is
not evidence the Codex Reviewer ever had.

`fit_images.py` is Claude-harness adaptation, not part of the ported Codex
bundle: it is an added file, and `figannot.py` stays byte-identical to its
Codex source. It resizes only when the long edge exceeds the limit, leaves a
compliant image byte-identical rather than re-encoding it, never converts to
JPEG, and skips a missing path — `accepted_control.png` exists only on
strict-3D iterations, so passing it unconditionally is correct. Never point it
at `$WORKDIR/img_iter$ITER.png`: that is the Drawer's output and the
evaluator's input, and only its staged `audit_view` copy may be fitted.

`composite.png` is deliberately absent from that list, and must stay absent.
Its pixels carry meaning beyond appearance: `figannot.py compose` writes the
native `W`, `H`, `draft_x` and `draft_w` into `composite_meta.json` and
`review_prompt.txt`, the Reviewer is told to treat those as hard coordinate
bounds, and `figannot.py draw` later opens `audit_view_<N>/composite.png` and
clamps the returned boxes against that same native metadata. Resizing the image
without resizing the metadata desynchronises the two: `draw` still exits 0, but
every box lands off-target and boxes near the right edge fall outside the
canvas. That was reproduced end to end - a 2480x920 composite fitted to
2000x742 drew a box displaced by the inverse of the 0.806 factor, with no error
anywhere in the chain. Since `annotated.png` is the next Drawer's only
positional repair brief, a silent offset there sends the repair to the wrong
region of the figure.

The host downscales `composite.png` on delivery anyway, and that is harmless:
the reader is told the factor and reports native coordinates, which is what
`composite_meta.json` and `draw` both expect. Only an on-disk resize breaks the
contract. Never pass `composite.png` - or `$WORKDIR/img_iter$ITER.png` - to
`fit_images.py`.

`image_fit_$ITER.json` records the native size, delivered size, and applied
factor for each fitted image; carry those numbers into `process.md`. Record
`composite.png`'s native size there too, so downstream analysis can still
separate reviews taken at native resolution from heavily downscaled ones.

Then dispatch the Reviewer:

```text
Task(
  subagent_type = "figmirror-reviewer",
  run_in_background = false,
  description = "figmirror reviewer iter $ITER",
  prompt = <the Reviewer task text below>
)
```

Reviewer task text:

```text
Role: figmirror-reviewer
Audit view: $WORKDIR/audit_view_$ITER
Iter: $ITER
Read review_prompt.txt, reviewer.md, and aesthetic-library.md from the audit
view. Far view: composite.png. Near views: reference_clean.png and
draft_fullres.png. Optional fixed 3D audit material: three-d-prompting.md plus
routed files under three-d/. This is a closed, stateless audit of the current
draft: do not read process state, Drawer notes, review history, or history-like
files. Use the L1/L2/L3 hierarchy: ground every claim in L1 or L2, never L3.
For geometry, use composite bbox coordinates, visual estimates, and any
diagnostics already staged in the audit view; separate global canvas shape, per-panel shape, and inter-panel gutter/packing.
Do not read outside this audit view. Do not write
files. Do not run code or Python. Return the JSON object specified in reviewer.md
and nothing else.

Open the visual inputs yourself with `Read`, exactly once each, in this order,
using the absolute paths given here:

1. $AV/composite.png
2. $AV/reference_clean.png
3. $AV/draft_fullres.png
4. $AV/accepted_control.png  (only when this line is present)

Before each `Read`, emit one line of the form `READ <i>/<total> <absolute
path>`, where `<total>` is the number of entries listed above - the
Orchestrator materialises it as the literal 3 or 4. Do not re-read an image you
have already read, do not read any image outside this list, and do not open any
file outside this audit view. If a final checkpoint asks you to recheck the
figure, reconsider the pixels you have already seen without performing another
image pass.
```

The `Task` tool carries a single text prompt; there is no attachment channel on
this harness, so the image bundle is expressed as the ordered absolute-path list
inside the task text and the Reviewer opens each path itself. Materialize every
path as a literal absolute path — no `$AV`, no `$WORKDIR`, no relative token.
Enumerate exactly three paths, in the order composite / reference / draft, and
append the `accepted_control.png` line as a fourth entry only when that file
exists. Never list `img_iter$ITER.png`. `draft_fullres.png` is its staged copy, and
that copy may have been fitted to the delivery limit, so the two are not
interchangeable: listing the original both duplicates the near view and mixes
unfitted pixels into the evidence set.

This is a weaker guarantee than an attachment channel: the Reviewer could read
fewer images, reorder them, or skip them. Be precise about what closes that gap
and what does not.

What is genuinely stronger here than on Codex: the `figmirror-reviewer` agent
definition grants `Read` and nothing else — no shell, no search tools, no
dispatch — so the role's "do not run code or Python" and "do not spawn
subagents" become structural instead of advisory. Codex has no per-role tool
allowlist at all (its role `tools` table accepts only `web_search` and
`experimental_request_user_input`), so those two lines were prose there.

What is NOT stronger, and must not be claimed: path scoping. Claude Code
permissions are session-level, not per-agent, so a deny rule aimed at the
Reviewer would equally blind the Orchestrator's protocol checks and the
Drawer's reads and writes; and the audit view path is generated per run, which
a static rule cannot address. "Do not read outside this audit view" therefore
remains exactly what it is on Codex: a prompt-level rule. Closed-bookness comes
from what the Orchestrator stages, not from a sandbox.

Stage nothing into the audit view beyond what this document already copies
there. The Reviewer role definition permits bounded memory files
(`anchors.md`, `changed.md`, `conflict_ledger.md`, `audit_iter<N-1>.json`)
"only when present in the audit view", and this loop never places them there —
that clause is inert, exactly as it is under the Codex orchestrator, and
nothing here should be read as authorising them.

Compliance with the read order itself is checked after the run, not enforced
during it. The `READ` lines live in the Reviewer's own transcript, so that
check is an offline pass over the run records, in the same place and with the
same standing as the Codex-side review-bridge verifier: it never gates a live
run.

### Reviewer task identity

`figannot.py review-decision` requires a non-empty `--reviewer-session` value
and rejects any value already recorded for an earlier attempt, so a reused
identifier is scored as an invalid attempt and two consecutive invalids fail the
sample closed. On Codex this value was the subagent's own child session ID. The
`Task` tool returns no such identifier, so the Orchestrator mints it instead:

```text
rev-iter<N>-slot<K>
```

`N` is the current Drawer round. `K` starts at 1 for the first Reviewer dispatch
of that round and increments on every subsequent dispatch for the same round,
whether it came from `review_same_draft` or from `retry_reviewer` — a retry must
carry a fresh identifier, never the one that just failed.

`K` is recomputable, and it must be recomputed rather than remembered, because
the Orchestrator's own context may be compacted mid-run. The authority is the
attempt ledger the helper maintains: `K = 1 + (number of entries in
$WORKDIR/review_attempts/ whose iter equals N)`. Do not derive it from
`process.md`; that file is only written at finalization and does not exist
during the loop.

Freeze it once minted. The same string names the incoming file
(`.review_incoming_rev-iter<N>-slot<K>.json`) and is passed as
`--reviewer-session`. Re-running the same `review-decision` invocation is an
idempotent replay only while the incoming file's bytes are unchanged: the helper
keys a replay on the session, iter, draft hash and payload hash together. If the
Reviewer produced different text, that is a new attempt and needs a new `K` —
reusing the identifier makes the helper record it as a reused session, which
counts as an invalid attempt, and two consecutive invalid attempts fail the
sample closed. Materialise the value as a literal in the traced command like
every other flag value.

The Orchestrator treats the Reviewer final text as the only audit payload. Save
that exact text with the `Write` tool to a temporary file outside the audit view;
do not interpolate it through shell quoting, and keep that file until the
adapter finishes post-run verification. The incoming filename must contain the exact Reviewer session ID.
For example, use `.review_incoming_rev-iter<N>-slot<K>.json`. If the final is missing or empty,
create an empty incoming file. `review-decision` records malformed, empty, and
non-object values as invalid attempts without fabricating a visual verdict.

For this command only, materialize the script path and every flag value as literals
in the command text recorded by the trace. Expand `PYTHON_CMD`, the absolute
workdir, the decimal iteration, the canonical draft path, the exact child ID,
`min_reviews`, and `max_iters` before execution. Do not leave `$WORKDIR`, `$ITER`, or another
shell variable in the traced command. Replace the sample values below with the
current run's literal values, then run the deterministic gate:

```bash
uv run --project /absolute/path/to/repo python /absolute/path/to/run-directory/tools/figannot.py review-decision \
  --review /absolute/path/to/run-directory/.review_incoming_rev-iter0-slot1.json \
  --workdir /absolute/path/to/run-directory \
  --iter 0 \
  --draft /absolute/path/to/run-directory/img_iter0.png \
  --reviewer-session rev-iter0-slot1 \
  --min-reviews 2 \
  --max-iters 5
```

Obey the returned `action` literally:

- `review_same_draft`: dispatch a new `figmirror-reviewer` with the same `$AV`
  and the same ordered image path list. Do not run `draw`, do not dispatch Drawer,
  and do not change `ITER`. The helper intentionally has not written the first
  clean result into the audit view, so the next Reviewer sees the same closed
  current-draft input without another review's result.
- `retry_reviewer`: retry the same review slot once with a fresh
  `figmirror-reviewer`, the same `$AV`, and the same ordered image path list. It
  does not count toward `min_reviews` and must never trigger Drawer. A second
  consecutive invalid result is persisted as retry exhaustion and exits
  nonzero.
- `draw`: run the command below. Apply the same trace-literal rule to this draw
  command: materialize the Python command, script path, and `--out-dir` as
  literal values in the recorded command; do not leave `$PYTHON_CMD`,
  `$WORKDIR`, `$ITER`, or another shell variable. The helper has already written
  the canonical audit and `review_feedback_<N>/review.json`; `annotated.png`
  plus `notes.md` in that feedback directory become the next Drawer's repair brief.
  The next Reviewer runs only after Drawer writes the changed `img_iter<N+1>.png`.
- `ship`: select this iter and finalize. Do not run `draw` or dispatch Drawer.
- `stop_at_cap`: do not run `draw` and do not dispatch Drawer. Apply the hard-cap
  selection policy and finalize the selected existing iteration.

```bash
uv run --project /absolute/path/to/repo python /absolute/path/to/run-directory/tools/figannot.py draw --out-dir /absolute/path/to/run-directory/review_feedback_0 --max-iters 5
```

The Reviewer restriction is prompt-level: it must not write files. Treat any
Reviewer-side write as a protocol anomaly and record it in `process.md`.

**Reviewer failure handling — MANDATORY, never fabricate an audit.** If the
Reviewer subagent fails, stalls/times out, does not return valid JSON, or
`review-decision` returns `retry_reviewer`, you MUST NOT invent
`quality_floor` / `fidelity` verdicts. A fabricated `passed:true` /
`verdict:close` audit silently disables the ONLY quality gate and ships broken
drafts. Instead:

1. Retry the same `figmirror-reviewer` role once with the same audit view, a new
   child task, and the same ordered absolute-path image list. Keep the same
   read-once, read-nothing-else rule. Do not expose the invalid result.
2. If it fails again, let `review-decision` persist the exhausted invalid
   attempt. Its nonzero exit happens inside a tool call and never becomes the
   orchestrator process's exit code — a model cannot set that — so on this
   harness the failure signal is a file: write the reason to
   `$WORKDIR/REVIEWER_FAILED`, record it in `process.md`, and stop. Do not
   finalize, do not select any iteration, and do not write a `status.json`
   claiming success. The runner treats the presence of `REVIEWER_FAILED` as a
   crashed sample.

Do not synthesize a `reviewer_unavailable` audit: it would have no valid Reviewer
terminal payload or matching ledger transition. A fabricated audit of any
verdict is a protocol violation.

## Finalization

Copy the selected iteration to final artifacts:

```bash
cp "$WORKDIR/figure_iter$SELECTED.py" "$WORKDIR/figure.py"
(cd "$WORKDIR" && bash -lc "$PYTHON_CMD figure.py")
```

Before the final run, ensure `figure.py` saves `figure.png`, `figure.pdf`, and
`output.png`; `output.png` is the evaluator-facing PNG and may be an identical
copy of `figure.png`. The final run must also write
`floor_selfcheck_final.txt`. Write `selection.md` with the selected iteration and
reason, `process.md` with a concise iteration changelog, and `status.json` with
machine-readable finalization status. If any final-bundle file is missing after
the run, repair `figure.py` or finalization and rerun it before exiting.
