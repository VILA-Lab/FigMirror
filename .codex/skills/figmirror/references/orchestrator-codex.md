# Codex Orchestrator Wiring

This reference is the Codex-only loop harness for `figmirror`.
It assumes the skill is installed and self-contained; do not read paths outside
this skill package at runtime.

Codex runtime shape: the top-level Codex process is Orchestrator only. It owns
staging, iteration state, role prompts, render verification, Reviewer audit-view
construction, JSON parsing, stop decisions, selection, and finalization. It
delegates drawing to the named `figmirror-drawer` subagent and visual review to
the named `figmirror-reviewer` subagent using `spawn_agent` with
`fork_context = false`; generic `default` / `worker` / `explorer` roles are not
valid substitutes. Candidate-pool generation is an optional host-level mode and
is outside the default shipped loop.

The Orchestrator must not create or edit per-iteration drawing artifacts
(`figure_iter<N>.py`, `img_iter<N>.png`, `notes_iter<N>.md`, or
`floor_selfcheck_iter<N>.txt`) itself. Those files are Drawer-owned protocol
outputs. After spawning Drawer, wait long enough for real production work before
declaring the role unavailable: wait at least 20 minutes for iter 0 and at least
10 minutes for later iters. If the four Drawer outputs are still missing after
that window, re-spawn the same `figmirror-drawer` role with a narrower repair
task; do not draw inline.

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

```bash
WORKDIR=/absolute/path/to/run-directory
SKILL_DIR=/absolute/path/to/figmirror
REFERENCES=$SKILL_DIR/references
USE_3D_INSERT=${USE_3D_INSERT:-0}
USE_3D_CANDIDATE_SCORER=${USE_3D_CANDIDATE_SCORER:-0}
PYTHON_CMD=${FIGMIRROR_PYTHON_CMD:-"uv run python"}
```

Use `PYTHON_CMD` for every Python invocation in this workflow, including
`tools/figannot.py` help/prepare/compose/draw, Drawer render checks, and final
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
cp "$REFERENCES/orchestrator-codex.md" "$WORKDIR/prompts/orchestrator-codex.md"
cp "$REFERENCES/aesthetic-library.md" "$WORKDIR/prompts/aesthetic-library.md"
cp "$REFERENCES/aesthetic-library.md" "$WORKDIR/prompts/aesthetic-library.md"
cp "$REFERENCES/aesthetic-library.md" "$WORKDIR/inputs/aesthetic-library.md"
cp "$SKILL_DIR/scripts/figannot.py" "$WORKDIR/tools/figannot.py"
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

## Stage 0: Reference Preprocessing

Before data generation, Drawer, or Reviewer, run the reference preprocessor as a
separate bounded agent/process using `prompts/preprocessor.md`. It must read
`inputs/reference_raw.png`, crop away removable whitespace/captions/page text or
neighboring panels, compare the before/after crop, and write:

- `inputs/reference_clean.png`
- `inputs/reference_crop_check.png`
- `inputs/reference_crop_report.md`

If the crop would remove figure information, retry with a larger box. If no safe
crop exists, preserve the raw image as `reference_clean.png` and record `no safe
crop` in the report.

## Per-Iteration Loop

Use the `max_iters` value provided by the caller/runner. If no value is
provided, default to `max_iters = 6`. Iterate `N = 0..max_iters-1`.
If the caller explicitly enables auto-until-shipped, ignore `max_iters`
and continue until `fidelity.verdict` is `ship`, cancellation, or a real
protocol/blocking failure:

1. Orchestrator spawns `agent_type = "figmirror-drawer"` with
   `fork_context = false`. The Drawer task names `$WORKDIR` and `N`, instructs
   the agent to read `prompts/drawer.md`, `prompts/aesthetic-library.md`,
   optional `prompts/three-d-prompting.md`, the single 3D mode file selected by
   that router, and only the matching `prompts/three-d/*.md` modules; optional
   `tools/score_3d_candidates.py` when quantitative 3D candidate diagnosis is
   enabled, `inputs/reference_clean.png`, `inputs/reference_crop_report.md` if
   present, `inputs/data.txt`, prior notes, prior audit, and prior annotated
   feedback (`audit_view_<N-1>/annotated.png` plus
   `audit_view_<N-1>/notes.md`) if `N > 0`.
2. Drawer writes `figure_iter<N>.py`, `img_iter<N>.png`, `notes_iter<N>.md`,
   and `floor_selfcheck_iter<N>.txt` in `$WORKDIR`. It must not launch `codex`,
   `claude`, or another model process.
   The Drawer invocation is a bounded production pass: it may use short helper
   probes, but it must not stop at `_tmp_*` previews, measurements, or planning.
   Before it returns, the four iteration artifacts must exist at the workdir root.
3. Orchestrator verifies the four iter artifacts are non-empty before any
   Reviewer handoff. If anything is missing before the patience window has
   elapsed, keep waiting on the same Drawer. Use `wait_agent` timeouts of at
   least 20 minutes for iter 0 and 10 minutes for later iters. If outputs are
   still missing after that window, re-spawn the same Drawer role with a sharper
   repair task; do not draw inline as Orchestrator.
4. Orchestrator stages `audit_view_<N>`, builds `composite.png` with
   `tools/figannot.py compose`, and spawns `agent_type = "figmirror-reviewer"`
   with `fork_context = false`. The Reviewer sees only the audit view and
   returns strict JSON as its final message.
5. Orchestrator parses the Reviewer final JSON, writes it to
   `audit_view_<N>/review.json` and `audit_iter<N>.json`, then runs
   `tools/figannot.py draw` to create `audit_view_<N>/annotated.png` and
   `audit_view_<N>/notes.md` for the next Drawer.
6. If `quality_floor.passed=false`, continue unless a non-auto hard cap is reached.
7. If `fidelity.verdict` is `ship`, select this iter.
8. If `fidelity.verdict=close`, run one more pass while budget remains, or always in auto mode.
9. If `fidelity.verdict=off`, continue while budget remains, or always in auto mode when there is a clear next revision.

At a non-auto hard cap, select the best floor-passing `close` iteration with the
lowest reference drift; otherwise select any floor-passing iteration with the
shortest violation list.

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

Spawn the Drawer as a named subagent:

```text
agent_type = "figmirror-drawer"
fork_context = false
```

The Drawer prompt must be self-contained and name the working directory, iter
index, staged prompt paths, input paths, prior audit path when present, and the
four required output files. It must also name the local render command from
`PYTHON_CMD`; the Drawer must use that command instead of guessing `python` or
`python3`.
Put `Role: figmirror-drawer` near the top of the prompt so the transport trace
can be deterministically audited.
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
`audit_view_<N-1>/annotated.png` and `audit_view_<N-1>/notes.md`. The annotated
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
and are non-empty. Missing artifacts before the patience window are not evidence
that Drawer is dead. Wait at least 20 minutes for iter 0 and 10 minutes for
later iters before repairing missing artifacts by re-spawning `figmirror-drawer`
with the same role and a narrower repair instruction.

## Reviewer Invocation

```bash
ITER=<N>
FIGANNOT="$WORKDIR/tools/figannot.py"
mkdir -p "$WORKDIR/audit_view_$ITER"
AV="$WORKDIR/audit_view_$ITER"
cp "$WORKDIR/inputs/reference_clean.png" "$AV/reference_clean.png"
cp "$WORKDIR/img_iter$ITER.png" "$AV/img_iter$ITER.png"
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
if [ "$ITER" -gt 0 ]; then
  cp "$WORKDIR/audit_iter$((ITER-1)).json" "$WORKDIR/audit_view_$ITER/audit_iter$((ITER-1)).json"
  if grep -q '^## Conflict ledger' "$WORKDIR/notes_iter$((ITER-1)).md" 2>/dev/null; then
    awk 'BEGIN{copy=0} /^## Conflict ledger/{copy=1} copy && /^## / && $0 !~ /^## Conflict ledger/{exit} copy{print}' \
      "$WORKDIR/notes_iter$((ITER-1)).md" > "$WORKDIR/audit_view_$ITER/conflict_ledger.md"
  fi
fi

# Build bounded Reviewer memory. `prepare` writes anchors.md from prior
# review.json confirmed-good fields and changed.md from the immediately previous
# boxed notes.
bash -lc "$PYTHON_CMD \"$FIGANNOT\" prepare \
  --workdir \"$WORKDIR\" \
  --iter \"$ITER\" \
  --out-dir \"$AV\""

ANCHORS="$AV/anchors.md"
CHANGED="$AV/changed.md"

bash -lc "$PYTHON_CMD \"$FIGANNOT\" compose \
  --ref \"$WORKDIR/inputs/reference_clean.png\" \
  --draft \"$WORKDIR/img_iter$ITER.png\" \
  --reviewer-md \"$REFERENCES/reviewer.md\" \
  --anchors-md \"$ANCHORS\" \
  --changed-md \"$CHANGED\" \
  --out-dir \"$AV\""
```

Then spawn the Reviewer:

```text
agent_type = "figmirror-reviewer"
fork_context = false
```

Reviewer task text:

```text
Role: figmirror-reviewer
Audit view: $WORKDIR/audit_view_$ITER
Iter: $ITER
Read review_prompt.txt, reviewer.md, and aesthetic-library.md from the audit
view. Far view: composite.png. Near views: reference_clean.png and
img_iter$ITER.png / draft_fullres.png. Optional prior audit:
audit_iter$((ITER-1)).json. Optional bounded memory: anchors.md, changed.md,
and conflict_ledger.md. Optional 3D insert: three-d-prompting.md plus routed
files under three-d/. Use the L1/L2/L3 hierarchy: ground every claim in L1 or
L2, never L3. For geometry, use composite bbox coordinates, visual estimates,
and any diagnostics already staged in the audit view; separate global canvas
shape, per-panel shape, and inter-panel gutter/packing. Do not read outside this
audit view. Do not write files. Do not run code or Python. Return the JSON object
specified in reviewer.md and nothing else.
```

The Orchestrator treats the Reviewer final JSON as the only audit payload. It
extracts the first JSON object from the subagent result, validates it with
`json.loads`, writes it verbatim to both `audit_view_<N>/review.json` and
`audit_iter<N>.json`, then runs:

```bash
bash -lc "$PYTHON_CMD \"$WORKDIR/tools/figannot.py\" draw --out-dir \"$WORKDIR/audit_view_$ITER\""
```

`audit_view_<N>/annotated.png` and `audit_view_<N>/notes.md` are required
artifacts. They are the next Drawer invocation's boxed visual feedback.

The Reviewer restriction is prompt-level: it must not write files. Treat any
Reviewer-side write as a protocol anomaly and record it in `process.md`.

**Reviewer failure handling — MANDATORY, never fabricate an audit.** If the
Reviewer subagent fails, stalls/times out, or does not return valid JSON, you
MUST NOT invent `quality_floor` / `fidelity` verdicts. A fabricated
`passed:true` / `verdict:close` audit silently disables the ONLY quality gate and
ships broken drafts. Instead:

1. Retry the SAME `figmirror-reviewer` spawn once with the same audit view.
2. If it fails again, FAIL CLOSED: write
   `{"iter": $ITER, "quality_floor": {"passed": false, "violation_kinds":
   ["reviewer_unavailable"], "summary": "reviewer subagent failed; gate could not
   run"}, "fidelity": {"verdict": "off"}}` to `audit_iter$ITER.json`, record the
   failure in `process.md`, and do NOT select that iter as final unless it is the
   only one that exists. A self-graded "pass" is a protocol violation, never allowed.

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
