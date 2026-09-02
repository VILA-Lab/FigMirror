# FigMirror — iter loop spec

Full per-step orchestration spec. SKILL.md has the skeleton; this file has
the details: bash commands, dispatch brief templates, audit JSON parsing,
drift calculation, fallback paths.

For strict 3D reproduction, the host may enable a bounded candidate-pool mode
before the normal Drawer/Reviewer loop. This is optional and not the default
path. Each candidate receives only the staged reference, L2 library, optional
3D insert, and its assigned output directory; do not expose source data, prior
candidate outputs, scores, or other candidates' notes across candidate prompts.

## Step 1: Stage the workdir

Pre-create every directory the loop will write into. Subagents Write into
existing dirs only (workspace permission quirk).

```bash
RUN_ID=$(date +%Y%m%d-%H%M%S)-$$-$RANDOM
WORKDIR=<cwd>/figmirror-runs/$RUN_ID
SKILL_DIR=<absolute-path-to-this-skill>
USE_3D_INSERT=${USE_3D_INSERT:-0}
USE_3D_CANDIDATE_SCORER=${USE_3D_CANDIDATE_SCORER:-0}

mkdir -p "$WORKDIR/inputs" "$WORKDIR/tools"
for N in 0 1 2 3 4 5; do
  mkdir -p "$WORKDIR/audit_view_$N"
done

cp "$SKILL_DIR/references/aesthetic-library.md" \
   "$WORKDIR/inputs/aesthetic-library.md"
```

Set `USE_3D_INSERT=1` only when the user asks for a 3D figure, the reference is
visibly 3D, or the parsed data requires 3D encoding. When enabled, copy the
conditional insert:

```bash
if [ "$USE_3D_INSERT" = "1" ]; then
  mkdir -p "$WORKDIR/inputs/three-d"
  cp "$SKILL_DIR/references/three-d-prompting.md" \
     "$WORKDIR/inputs/three-d-prompting.md"
  cp "$SKILL_DIR"/references/three-d/*.md "$WORKDIR/inputs/three-d/"
fi
if [ "$USE_3D_CANDIDATE_SCORER" = "1" ]; then
  test "$USE_3D_INSERT" = "1"
  cp "$SKILL_DIR/scripts/score_3d_candidates.py" \
     "$WORKDIR/tools/score_3d_candidates.py"
fi
```

Then save the user's reference image to `inputs/reference_raw.png` and
also copy it to `inputs/reference_clean.png` as a temporary first-paint
placeholder. Save the parsed data to `inputs/data.txt` (use Read on the
user's attached bytes + Write to path; or filesystem `cp` if they provided
a path).

## Step 2: Reference preprocessing

Dispatch `figure-preprocessor` before data echo, data generation, Drawer, or
Reviewer. Brief:

```
Stage 0 reference preprocessing. Workdir: <WORKDIR>

Inputs:
  - inputs/reference_raw.png          (original user upload)
  - inputs/reference_clean.png        (temporary copy; overwrite with clean crop)

Crop away separable captions, page text, neighboring panels, screenshot margins,
and excess whitespace. Preserve every axis label, tick label, legend, colorbar,
panel title/letter, annotation, inset, and target multi-panel group. Compare the
raw and cropped image before finalizing; retry if figure information was cut.

Produce:
  - inputs/reference_clean.png
  - inputs/reference_crop_check.png
  - inputs/reference_crop_report.md

Reply ≤120 words.
```

If the crop would remove figure information, retry with a larger box. If no safe
crop can be determined, keep the raw image as `reference_clean.png` and explain
`no safe crop` in the report.

## Step 3: Decision-7 data echo

Before dispatching anything, echo the parsed data structure back to the
user:

```
I parsed your data as:
- N rows × M columns
- Columns: [<list>]
- NaN cells: <count> at <locations if few>
- Sample of first row: <values>

Does this look right? If yes, I'll proceed to render.
```

Skip this confirmation only if the user pre-authorized. Either way, write
the echo to `<WORKDIR>/data_echo.md`.

## Step 4: Iter loop

Use the caller-provided `max_iters`; default to 6 when the caller gives no
explicit limit. If the caller enables auto-until-shipped, ignore `max_iters`
and continue until `ship` or a real blocker. Otherwise, for `N = 0..max_iters-1`:

### 4a. Dispatch the Drawer

Use the Task tool with `subagent_type: figure-illustrator`. Brief:

```
Iter <N>. Workdir: <WORKDIR>

Stable inputs:
  - inputs/reference_clean.png       (L1, Stage-0 cleaned crop)
  - inputs/reference_crop_report.md
  - inputs/data.txt
  - inputs/aesthetic-library.md  (L2)
  {3D only:} - inputs/three-d-prompting.md
  {3D only:} - one mode file and routed modules from inputs/three-d/*.md
  {3D candidate diagnosis only:} - tools/score_3d_candidates.py

Prior iters' artifacts are in workdir — reference them and edit
incrementally.

For strict 3D, continue from the current accepted iteration when it differs from
the most recent render.

If you reject or reinterpret Reviewer feedback after re-checking L1/L2, record a
compact `## Conflict ledger` in `notes_iter<N>.md` with the property, prior
anchor, Reviewer claim, new check, decision, and next-review request.

For strict 3D `N > 0`, keep a rendered accepted-control candidate under final
export settings. Compare it against each probe; if every probe regresses a
primary 3D dimension, export the accepted control and mark the repair unresolved.

Produce: figure_iter<N>.py, img_iter<N>.png, notes_iter<N>.md,
floor_selfcheck_iter<N>.txt

Reply ≤200 words.
```

### 4b. Stage the Reviewer's audit view

```bash
cp "$WORKDIR/inputs/reference_clean.png"      "$WORKDIR/audit_view_<N>/"
cp "$WORKDIR/img_iter<N>.png"                 "$WORKDIR/audit_view_<N>/"
cp "$WORKDIR/inputs/aesthetic-library.md" "$WORKDIR/audit_view_<N>/"
[ -f "$WORKDIR/inputs/three-d-prompting.md" ] && \
  cp "$WORKDIR/inputs/three-d-prompting.md"   "$WORKDIR/audit_view_<N>/"
[ -d "$WORKDIR/inputs/three-d" ] && \
  mkdir -p "$WORKDIR/audit_view_<N>/three-d" && \
  cp "$WORKDIR"/inputs/three-d/*.md "$WORKDIR/audit_view_<N>/three-d/"
[ -f "$WORKDIR/inputs/three-d-prompting.md" ] && [ <N> -gt 0 ] && \
  [ -n "${ACCEPTED_ITER:-}" ] && \
  cp "$WORKDIR/img_iter$ACCEPTED_ITER.png" "$WORKDIR/audit_view_<N>/accepted_control.png"
[ <N> -gt 0 ] && cp "$WORKDIR/audit_iter$((N-1)).json" \
                    "$WORKDIR/audit_view_<N>/"
if [ <N> -gt 0 ] && grep -q '^## Conflict ledger' "$WORKDIR/notes_iter$((N-1)).md"; then
  awk 'BEGIN{copy=0} /^## Conflict ledger/{copy=1} copy && /^## / && $0 !~ /^## Conflict ledger/{exit} copy{print}' \
    "$WORKDIR/notes_iter$((N-1)).md" > "$WORKDIR/audit_view_<N>/conflict_ledger.md"
fi
```

NEVER copy `data.txt` or full `notes_iter*.md` into the audit view. Only the
bounded `## Conflict ledger` excerpt may cross this boundary; vision-only audit
preserves reviewer independence.

### 4c. Dispatch the Reviewer

Use the Task tool with `subagent_type: figure-critic`. Brief:

```
Iter <N>. Audit view (your full world): <WORKDIR>/audit_view_<N>/
  - reference_clean.png
  - img_iter<N>.png
  - aesthetic-library.md
  {3D only:} - three-d-prompting.md
  {strict 3D N>0:} - accepted_control.png (regression control, not L1)
  {N>0:} - audit_iter<N-1>.json
  {optional:} - conflict_ledger.md (triage list only; re-check L1 directly)

Output ONE JSON object per your schema. No prose, no fence.
```

### 4d. Parse the audit JSON

Save the Reviewer's raw return as `audit_iter<N>.raw.txt`, then extract
the JSON. Opus sometimes wraps in fences; handle both forms:

```python
import re, json
raw = open(f"{WORKDIR}/audit_iter{N}.raw.txt").read()
m = (re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
     or re.search(r"(\{.*\})", raw, re.DOTALL))
audit = json.loads(m.group(1))
with open(f"{WORKDIR}/audit_iter{N}.json", "w") as f:
    json.dump(audit, f, indent=2)
```

If extraction fails, re-dispatch the Reviewer ONCE with an addendum
telling it to drop the fence. If the second attempt also fails, record in
`process.md` and treat the iter as floor-failed.

### 4e. 3D Meta Review Gate

When the 3D insert is staged, the caller acts as process-level Meta Reviewer.
It does not replace `figure-critic`; it checks whether the Drawer/Reviewer loop
is coherent before accepting a repair, selecting a final render, or declaring
`ship`.

For strict 3D reproduction, mark the iteration meta-invalid and continue or
rerender a narrower repair when Reviewer JSON is invalid or lacks
`three_d_scorecard`, strict 3D scorecard uses non-canonical field names instead
of `camera_box_aspect` and `text_export_floor`, focus skips the lowest primary
3D dimensions, Drawer ignores bounded feedback without an L1/L2 conflict
ledger, `N > 0` strict repair lacks a rendered accepted-control comparison or
changes multiple primary registers without separate probes, a repair regresses
topology, footprint, camera/box aspect, occupancy, or export floor, a topology
or footprint repair changes camera, box aspect, final-export crop, subject
occupancy, mark overlays, palette semantics, or export floor without a separate
accepted registration probe, a later render wins only because it is later, or
`ship` is below thresholds or hard gates.

Before adding any new 3D rule to a repair brief, apply a generalization gate:
the rule must be triggered by visible L1 evidence, apply beyond one example or
be scoped to a visible 3D class, avoid mechanical checkboxes, and avoid case
names, paths, prior scores, or run provenance.

### 4f. Apply the decision rule

```python
if audit["quality_floor"]["passed"] and audit["fidelity"]["verdict"] == "ship":
    chosen_iter = N
    break
elif N == max_iters - 1 and not auto:
    break  # fall through to select-best
else:
    continue  # iter N+1
```

Three inputs (`floor.passed`, `verdict`, budget remaining), one output
(continue or stop). No score arithmetic.

## Step 4: Select-best fallback

If the loop exits at the hard cap without `ship`, run a final
PIL-grounded selection pass:

1. For each iter, compute drift distance =
   `abs(draft.aspect - ref.aspect) / ref.aspect` plus
   `abs(draft_visible_spines - ref_visible_spines)`.
2. Among iters with `quality_floor.passed = true` AND
   `fidelity.verdict = "close"`, pick the one with **lowest drift
   distance** (NOT the most recent).
3. If no `close` iters exist, pick any floor-passing iter with the
   shortest violation list.

Document the choice in `selection.md` with drift distances tabulated.

## Step 5: Write canonical artifacts

```bash
cp "$WORKDIR/figure_iter${chosen}.py" "$WORKDIR/figure.py"
cp "$WORKDIR/img_iter${chosen}.png"   "$WORKDIR/figure.png"

# Re-render PDF using the chosen-iter script. The Drawer's script
# template sets pdf.fonttype=42 already.
( cd "$WORKDIR" && uv run --with pillow --with matplotlib --with numpy \
    python -c "
import matplotlib; matplotlib.use('Agg')
import runpy
runpy.run_path('figure.py', run_name='__main__')
import matplotlib.pyplot as plt
plt.savefig('figure.pdf')
" )
```

Write `process.md` documenting the per-iter trajectory (verdict counts,
themes addressed, drawer pushbacks). Write `selection.md` only if the
fallback fired.

## Step 6: Surface the result to the user

- Render `figure.png` inline (use the Read tool).
- Show paths to `figure.py`, `figure.png`, `figure.pdf`.
- One- or two-sentence trajectory summary ("shipped at iter 3 after `off
  → close → ship`"; or "fell back to iter 4 of 6 with `close` verdict").
- Pointer to `<WORKDIR>/process.md` for per-iter detail.

Do NOT show audit JSONs, drawer notes, or per-iter scripts unless the
user asks — those are debugging artifacts.

## Subagent dispatch fallback

Prefer `subagent_type: figure-preprocessor` / `figure-illustrator` /
`figure-critic`. If those
names don't resolve in the host environment (project agents may not
auto-register without a session restart; user agents only register after
`scripts/install_claude_skill.py` runs), fall back to:

- `subagent_type: general-purpose`
- Brief prefix: "Read your role from
  `<absolute-path-to>/figure-preprocessor.md`,
  `<absolute-path-to>/figure-illustrator.md`, or
  `<absolute-path-to>/figure-critic.md` and
  follow it." then proceed with the regular brief above.

Either path uses the same role-prompt content; the agent .md file works
as a system-prompt source either way.

## Workspace permission quirk

In some environments, subagents' `Write` to absolute paths or
`Bash mkdir` is denied at the workspace path. Two mitigations:

- **Pre-create all directories before dispatch** (see Step 1).
- **Heredoc-from-cwd workaround** for absolute-path Write denials inside
  a subagent: `cd "$WORKDIR" && cat > figure_iter<N>.py <<'EOF' ... EOF`.

Pre-create handles 95% of cases; the heredoc is the second line of
defense.

## Artifact layout (after a complete run)

```text
<WORKDIR>/
  inputs/
    reference_raw.png
    reference_clean.png
    reference_crop_check.png
    reference_crop_report.md
    data.txt
    aesthetic-library.md
    three-d-prompting.md     # router, only for 3D runs
    three-d/                 # mode files and routed modules, only for 3D runs
  data_echo.md
  figure_iter0.py
  img_iter0.png
  notes_iter0.md
  floor_selfcheck_iter0.txt
  audit_view_0/
    reference_clean.png
    img_iter0.png
    aesthetic-library.md
  audit_iter0.raw.txt    # raw Reviewer output (forensics)
  audit_iter0.json       # clean parsed JSON
  ... (per-iter artifacts repeat through the chosen iter)
  figure.py              # canonical chosen-iter script
  figure.png             # canonical chosen-iter render
  figure.pdf             # type-42 PDF
  process.md             # per-iter trajectory summary
  selection.md           # only when select-best fallback fired
```
