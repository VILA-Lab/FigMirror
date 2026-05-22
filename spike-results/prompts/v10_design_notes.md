# v10 design notes — addressing the v9 drift failure

> Captures the structural fixes for v9's monotonic drift problem. NOT a full prompt
> rewrite yet — this is the design before the rewrite, surfaced for review.

---

## What v9 actually did wrong (hard data)

Aspect ratio trajectory across 6 iters of the v9 loop, with reference = 1.95:

```
ref      1.95   ←  ground truth
iter0    1.95   ←  doer's PIL measurement, EXACT match
iter1    1.82   ←  reviewer pushed: "more headroom"
iter2    1.55   ←  reviewer pushed: "less squat" → -21% off reference
iter3    1.57   ←  locked
iter4    1.57   ←  locked
iter5    1.57   ←  selected (now reverted to iter1, see selection.md)
```

The figure was *correct on aspect at iter0*, then pushed off by **21% over 2
reviewer rounds**, then frozen at the wrong value for the rest of the loop. The
reviewer never measured aspect (vision only, prompt forbade tools), and never
affirmed "aspect is correct, keep it" — so the doer's correct measurement was
fair game and got modified away.

This is a *monotonic drift* failure, not a noise failure. The loop accumulated
deltas instead of converging.

## Root causes

### Cause 1 — Reviewer has no tools

v9_reviewer.md explicitly says "no tools, vision only." That was a deliberate
move to keep the reviewer focused on taste judgment. But it has a side effect:
the reviewer cannot verify *any pixel-level claim* before stating it. Aspect
ratio, spine count, exact font size, gridline weight — these require either
measurement or a willingness to say "I'm not sure." The v9 reviewer instead
made confident claims it couldn't verify (e.g. iter2: "reference frames each
panel with visible spines (all 4)" — actually only left+bottom).

The lesson is NOT "reviewer should have all tools." It is "reviewer should be
allowed to ground specific perceptual claims with bounded measurement, and
should be required to say 'unsure' otherwise."

### Cause 2 — Reviewer has no `anchor.what_is_right` output

v9 schema:
```
quality_floor.violation_kinds  ← what's wrong (floor)
focus_themes                   ← what to change (themes)
```

Both fields point at *deltas to apply*. There is no field that says *do not
modify this*. The doer therefore has no signal that property X is already
correct, so when iter N+1's themes touch property X (even tangentially), the
doer modifies it.

The lesson is the same one that the senior-code-reviewer pattern bakes in: a
good reviewer's first move is "here's what's right, keep it" — *then* "here's
what to change."

### Cause 3 — Reviewer is fresh-context per iter

Each reviewer subprocess gets only `reference_clean.png` + `img_iter<N>.png`.
It does not see prior reviewer JSONs. So if iter N-1 said "go bolder" and the
doer went bolder, iter N's reviewer (with fresh context) sees the bolder
draft as the new baseline and may say "go lighter" without knowing it's
reversing its own prior critique. This is the oscillation failure mode from the
v9 final report (iter2/iter3 type-weight oscillation).

## The v10 fix — three concrete prompt changes

### Fix A — Reviewer gets bounded measurement capability

The reviewer subprocess gets:
- Read tool (already implicit — it views images)
- Bounded `python -c` via Bash, restricted to PIL operations on the two images
  in `audit_view/`. No file writes, no network, no Bash access outside that.

The reviewer prompt is updated:
> Before stating any claim about a *measurable* property of the images
> (aspect ratio, font height in pixels, spine count and color, exact line
> width), you MUST measure it with PIL. Examples:
> ```python
> from PIL import Image
> ref = Image.open("reference_clean.png")
> draft = Image.open("img_iter<N>.png")
> print("ref aspect", ref.size[0]/ref.size[1], "draft aspect", draft.size[0]/draft.size[1])
> ```
> If you do not measure, you may NOT make a confident claim about that
> property. Either measure it, or write "I cannot confirm by eye" and skip
> the theme.

Tool-use is permitted but *not unbounded*. The reviewer is still primarily a
taste judge; tools exist only to ground specific claims.

### Fix B — Schema gains `anchor.what_is_right[]`

The reviewer schema becomes:

```json
{
  "iter": <int>,
  "anchor": {
    "what_is_right": [
      "<≤1 sentence per item; 3-7 items; explicitly call out properties the
       doer must preserve in subsequent iters>"
    ]
  },
  "quality_floor": { ... unchanged ... },
  "fidelity": { ... unchanged ... },
  "focus_themes": [ ... unchanged, cap=5 ... ]
}
```

The reviewer is REQUIRED to populate `what_is_right` with at least 3 items per
iter (even if the figure is mostly off — there's always something right, even
if it's "the choice to use a 2x3 panel grid"). The orchestrator forwards
`anchor.what_is_right` to the next iter's drawer as a "hard preserve" list:

> The following properties of the prior iter were affirmed correct by the
> reviewer. Do NOT change them in this iter. If a focus_theme appears to
> require changing one of these, push back via notes_iter<N>.md instead of
> blindly making the change.

This is the structural fix for monotonic drift.

### Fix C — Reviewer gets the prior audit JSON for damping

The orchestrator passes `audit_iter<N-1>.json` (if exists) to the reviewer
subprocess. The reviewer prompt adds:

> If a focus_theme you are about to write would push the doer in the OPPOSITE
> direction of a focus_theme from the prior reviewer audit, do not write it.
> Either accept the new state, or recommend continued movement in the same
> direction. Damping is more important than perfectionism.

This breaks the oscillation pattern. Combined with Fix B's `what_is_right`,
oscillation should drop from ~2 wasted iters to 0.

## Drawer-side companion changes

In v9_drawer.md:

1. New rule: "If a reviewer focus_theme contradicts what your own PIL sampling
   shows about the reference, trust your sampling. Note the discrepancy in
   `notes_iter<N>.md` but do NOT change the figure."

2. New rule: "Properties listed under reviewer's `anchor.what_is_right` are
   off-limits for modification this iter. If you believe a focus_theme requires
   changing one of these, surface the conflict in `notes_iter<N>.md` and leave
   the property alone."

3. New iter-0 invariant: "After your initial PIL measurement of the reference,
   record the *anchor measurements* in `notes_iter0.md`: reference aspect ratio,
   typical font heights in pixels, spine count, gridline weight. These are your
   own first-principles anchor; consult them whenever a reviewer theme touches a
   measurable property."

## Open questions before running v10

- Should the reviewer's `anchor.what_is_right` be required to grow monotonically
  (each iter affirms at least everything the prior iter affirmed, plus possibly
  more)? That would give even stronger drift protection but might over-constrain.
- Should the orchestrator do its own PIL aspect-ratio check at every iter as a
  belt-and-suspenders against the reviewer slipping?
- Worth running v10 against the same v8 inputs to compare aspect-trajectory and
  iter-1-vs-iter-N quality? That's the cleanest A/B against v9.

## Estimated impact

If Fix A + Fix B land cleanly, v9's `iter0 (1.95) → iter5 (1.57)` trajectory
should become closer to `iter0 (1.95) → iter5 (1.95)` — i.e. aspect locked
from iter0 forward, with fixes accumulating only on properties NOT in
`anchor.what_is_right`. That alone would have made v9's iter1 the natural
ship candidate instead of the manual post-mortem.
