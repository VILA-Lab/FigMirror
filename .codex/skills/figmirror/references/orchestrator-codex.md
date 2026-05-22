# Codex Orchestrator Wiring

This reference is the Codex-only loop harness for `figmirror`.
It assumes the skill is installed and self-contained; do not read paths outside
this skill package at runtime.

Codex runtime shape: the top-level Codex process acts as both Orchestrator and
Drawer. It owns iteration state, drawing, local floor self-checks, Reviewer audit
staging, JSON parsing, and stop decisions. Stage-0 reference preprocessing and
Reviewer audit remain separate bounded passes; the Reviewer uses fresh-context
`codex exec` with attached images. Candidate-pool generation is an optional
host-level mode and is outside the default shipped loop.

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
CODEX=${CODEX:-/Applications/Codex.app/Contents/Resources/codex}
if [ ! -x "$CODEX" ]; then
  CODEX=$(command -v codex || true)
fi
test -x "$CODEX"
```

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

1. Orchestrator/Drawer reads `prompts/drawer.md`,
   `prompts/aesthetic-library.md`, optional `prompts/three-d-prompting.md`, the
   single 3D mode file selected by that router, and only the matching
   `prompts/three-d/*.md` modules; optional `tools/score_3d_candidates.py` when
   quantitative 3D candidate diagnosis is enabled,
   `inputs/reference_clean.png`, `inputs/reference_crop_report.md` if present,
   `inputs/data.txt`, prior notes, and prior audit if `N > 0`.
2. Orchestrator/Drawer writes `figure_iter<N>.py`, `img_iter<N>.png`,
   `notes_iter<N>.md`, and `floor_selfcheck_iter<N>.txt` directly in this Codex
   process. It must not launch `codex exec`, `codex`, `claude`, or another
   model process for the Drawer in the default path.
3. Orchestrator verifies the four iter artifacts are non-empty before any
   Reviewer handoff. If anything is missing, repair it in the same Codex process.
4. Orchestrator stages `audit_view_<N>` and launches the fresh-context Reviewer.
5. Orchestrator parses `audit_iter<N>.json`.
6. If `quality_floor.passed=false`, continue unless a non-auto hard cap is reached.
7. If `fidelity.verdict` is `ship`, select this iter.
8. If `fidelity.verdict=close`, run one more pass while budget remains, or always in auto mode.
9. If `fidelity.verdict=off`, continue while budget remains, or always in auto mode when there is a clear next revision.

At a non-auto hard cap, select the best floor-passing `close` iteration with the
lowest reference drift; otherwise select any floor-passing iteration with the
shortest violation list.

## 3D Meta Review Gate

When the 3D insert is staged, the Orchestrator acts as the process-level Meta
Reviewer. It does not replace the fresh-context Reviewer; it checks whether the
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

Run the Drawer role in the same top-level Codex process as the Orchestrator.
For `N = 0`, perform the Drawer prompt's anchor-measurement pass before writing
the first figure. For `N > 0`, copy `figure_iter<N-1>.py` to
`figure_iter<N>.py` and edit incrementally. For strict 3D, source that copy from
the current accepted iteration when it differs from `N-1`. Respect
`audit_iter<N-1>.json.anchor.what_is_right` as a preserve list, address
`quality_floor.violation_kinds` before fidelity themes, and explain any conflict
between Reviewer feedback and measured anchors in `notes_iter<N>.md`. If there
is a real conflict, the notes must include a compact `## Conflict ledger` section
so the next Reviewer can spend extra effort on that property.

For strict 3D repairs with `N > 0`, keep a rendered accepted-control candidate
under final export settings and compare it against each probe before Reviewer
handoff. If every probe regresses topology, footprint, camera/aspect,
composition/occupancy, or export floor, export the accepted control as the
iteration result and mark the repair unresolved in notes.

Before launching the Reviewer, verify that `figure_iter<N>.py`,
`img_iter<N>.png`, `notes_iter<N>.md`, and `floor_selfcheck_iter<N>.txt` exist
and are non-empty. Repair missing artifacts in-process.

## Reviewer Invocation

```bash
ITER=<N>
mkdir -p "$WORKDIR/audit_view_$ITER"
cp "$WORKDIR/inputs/reference_clean.png" "$WORKDIR/audit_view_$ITER/reference_clean.png"
cp "$WORKDIR/img_iter$ITER.png" "$WORKDIR/audit_view_$ITER/img_iter$ITER.png"
cp "$REFERENCES/aesthetic-library.md" "$WORKDIR/audit_view_$ITER/aesthetic-library.md"
THREE_D_MSG=""
EXTRA_REVIEWER_IMAGES=()
if [ -f "$WORKDIR/prompts/three-d-prompting.md" ]; then
  cp "$WORKDIR/prompts/three-d-prompting.md" "$WORKDIR/audit_view_$ITER/three-d-prompting.md"
  if [ -d "$WORKDIR/prompts/three-d" ]; then
    mkdir -p "$WORKDIR/audit_view_$ITER/three-d"
    cp "$WORKDIR"/prompts/three-d/*.md "$WORKDIR/audit_view_$ITER/three-d/"
  fi
  THREE_D_MSG=" 3D insert (READ WHEN PRESENT): audit_view_$ITER/three-d-prompting.md, then exactly one mode file under audit_view_$ITER/three-d/ and only the modules routed by that mode."
  if [ "$ITER" -gt 0 ] && [ -n "${ACCEPTED_ITER:-}" ]; then
    cp "$WORKDIR/img_iter$ACCEPTED_ITER.png" "$WORKDIR/audit_view_$ITER/accepted_control.png"
    THREE_D_MSG="$THREE_D_MSG Accepted control: audit_view_$ITER/accepted_control.png."
    EXTRA_REVIEWER_IMAGES=(-i "$WORKDIR/audit_view_$ITER/accepted_control.png")
  fi
fi
if [ "$ITER" -gt 0 ]; then
  cp "$WORKDIR/audit_iter$((ITER-1)).json" "$WORKDIR/audit_view_$ITER/audit_iter$((ITER-1)).json"
  if grep -q '^## Conflict ledger' "$WORKDIR/notes_iter$((ITER-1)).md" 2>/dev/null; then
    awk 'BEGIN{copy=0} /^## Conflict ledger/{copy=1} copy && /^## / && $0 !~ /^## Conflict ledger/{exit} copy{print}' \
      "$WORKDIR/notes_iter$((ITER-1)).md" > "$WORKDIR/audit_view_$ITER/conflict_ledger.md"
  fi
fi

REVIEWER_PROMPT=$(cat "$REFERENCES/reviewer.md")
USER_MSG="Iter: $ITER. Reference: audit_view_$ITER/reference_clean.png. Draft: audit_view_$ITER/img_iter$ITER.png. Library (READ THIS FIRST): audit_view_$ITER/aesthetic-library.md.${THREE_D_MSG} Prior audit (if iter>0): audit_view_$ITER/audit_iter$((ITER-1)).json. Conflict ledger may be present: audit_view_$ITER/conflict_ledger.md. Use the L1/L2/L3 hierarchy: ground every claim in L1 (reference) or L2 (library), never L3 (opinion). If accepted_control.png is present, use it only to flag regressions before accepting a 3D repair. For PIL-unreliable value estimates (spine color/width, gridline width, font weight), use L2 as the fallback class vocabulary; for spine count/sides, axis topology, gridline direction, and spacing ratios, re-check L1 directly. DO NOT write any files. Output the JSON object specified in your instructions and nothing else."
FULL_PROMPT=$(printf '%s\n\n---\n\n%s' "$REVIEWER_PROMPT" "$USER_MSG")

REVIEWER_ARGS=(
  exec
  --ephemeral
  -C "$WORKDIR/audit_view_$ITER"
  -i "$WORKDIR/audit_view_$ITER/reference_clean.png"
  -i "$WORKDIR/audit_view_$ITER/img_iter$ITER.png"
  "${EXTRA_REVIEWER_IMAGES[@]}"
  -o "$WORKDIR/audit_iter$ITER.json"
  -
)
if [ -n "${FIGMIRROR_REVIEWER_MODEL:-}" ]; then
  REVIEWER_ARGS=(exec -m "$FIGMIRROR_REVIEWER_MODEL" "${REVIEWER_ARGS[@]:1}")
fi
if [ "${FIGMIRROR_REVIEWER_BYPASS_SANDBOX:-0}" = 1 ]; then
  REVIEWER_ARGS=(exec --dangerously-bypass-approvals-and-sandbox "${REVIEWER_ARGS[@]:1}")
fi

printf '%s' "$FULL_PROMPT" | "$CODEX" "${REVIEWER_ARGS[@]}" \
  2> "$WORKDIR/audit_iter$ITER.stderr"
```

If you wrap the Reviewer command in additional shell logic, avoid assigning to a
variable named `status`; zsh treats `status` as read-only. Use `rc=$?` or
`reviewer_rc=$?` for exit-code capture.

The Reviewer restriction is prompt-level: it must not write files. Treat any
Reviewer-side write as a protocol anomaly and record it in `process.md`.

## Finalization

Copy the selected iteration to final artifacts:

```bash
cp "$WORKDIR/figure_iter$SELECTED.py" "$WORKDIR/figure.py"
PYTHON=${PYTHON:-$(command -v python3 || command -v python)}
(cd "$WORKDIR" && "$PYTHON" figure.py)
```

The final script must generate `figure.png`, `figure.pdf`, and
`floor_selfcheck_final.txt`. Write `selection.md` with the selected iteration and
reason, and `process.md` with a concise iteration changelog.
