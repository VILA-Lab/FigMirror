# v9 Findings — what v8 actually got wrong, and what to import from Anthropic's frontend-design skill

> Written before the v9 spike runs. Captures (a) the diagnosis on the v8 output, (b) the
> structural lessons borrowed from the upstream `frontend-design` SKILL.md and the
> "Prompting for frontend aesthetics" cookbook, and (c) the design moves for v9.

---

## 1. What v8 actually got wrong

The chosen v8 figure (`spike-results/claude-code-subagent-v8/img_iter2.png`) is, on style,
genuinely close to the reference — palette, spines, gridlines, legend frames, line/marker
treatment, and panel-grid layout all read as "from the same paper." The user's verdict was
"image quality is high, even closer to ground truth than GPT — but it has overlap defects."

The v8 audit at iter0 already correctly identified all the major defects, and the same
defects were still present at iter2:

| Defect | Where | Status at iter2 |
| --- | --- | --- |
| V2 data labels (`0.97`, `↑14%`) crash into the next panel's title (`Gemini 2.5-Pro`, `GPT-5`) | top-row column gutters | still present |
| Bottom-row `ε` xlabel and `4 / 8 / 16` ticks clipped off canvas | bottom margin | still present |
| Spurious `ε` xlabel on the top row (reference has none) | both rows | still present |
| Grey V1 value labels overlap the `4 / 8 / 16` x-tick text | every panel | still present |
| `hspace` too tight — bottom-row titles butt against top-row spine | inter-row gap | still present |
| `wspace` too tight — even ignoring titles, V2 labels touch neighbor axes | column gutters | still present |
| Top legend renders dot+line glyphs; reference uses pure colored line swatches | top-left legend | still present |

**Therefore the bottleneck is NOT perception (the audits saw the right things) and NOT
the painter's craft (palette/spine/legend frame are correct). The bottleneck is that the
loop never closed on these specific defects within the iteration budget.** The v8 audit
even gave the final figure 4/10, yet that figure was selected as the deliverable.

### Structural root causes

1. **No quality floor.** The audit returned a free-form ranked list and a 0–10 score.
   "Score 4/10 with 7 named defects" was treated as acceptable to ship because there was
   no rule that said "any text-on-text or text-on-tick collision is a hard reject."
   Defects that are visually disqualifying (overlap) were ranked alongside cosmetic ones
   (legend frame border 5% off-hue) and the loop ran out of budget before fixing either
   class.

2. **The doer's geometry contract is wrong.** v8 forces `figsize × dpi == reference pixel
   dimensions` (the "R4" rule). The reference image is rendered at an effective DPI
   substantially below 180 for its physical size, so this rule produces a 7.5 in × 3.9 in
   canvas at 180 dpi — and on that small canvas, 9 pt annotations are 22 px tall, which
   is what eats into the title band and the tick row. **The font-pt → px conversion
   formula in the v8 prompt assumes the reference's effective DPI equals the output DPI,
   which it does not.** The user's complaint "字体偏大、还加粗了" is a downstream symptom of
   this miscalibration — the fonts are not literally bold, they are pixel-bold relative
   to the canvas because the canvas is too small for those font points.

3. **The reviewer is one persona, not two.** The v8 prompt's audit-via-Bash step uses a
   single fresh-context audit that mixes "is this faithful to the reference's style"
   with "is this readable as a paper figure at all." The two questions need different
   schemas: the first is a graded comparison, the second is a pass/fail floor. Mixing
   them lets the doer talk the reviewer into a 4/10 ship.

4. **Inspiration vocabulary is the reference image, only.** When the audit says "the
   legend should look more like the reference," the doer has no second anchor to triangulate
   from. Anthropic's frontend-design skill never relies on a single reference — it always
   pairs a constraint with a *menu of named exemplars* the model can pull from. We have no
   such menu for "what does a top-conference figure legend look like in general."

---

## 2. What to import from Anthropic's frontend-design skill and the cookbook

Studying:
- the upstream `frontend-design` SKILL.md
  (`~/.claude/plugins/marketplaces/claude-plugins-official/plugins/frontend-design/skills/frontend-design/SKILL.md`)
- the "Prompting for frontend aesthetics" cookbook
  (https://platform.claude.com/cookbook/coding-prompting-for-frontend-aesthetics)
- the distilled `<frontend_aesthetics>` system-prompt block

three construction patterns recur, and they map cleanly onto our problem:

### Pattern A — Identity is built by naming the failure mode, not the title

The cookbook's distilled prompt opens with:

> You tend to converge toward generic, "on distribution" outputs. In frontend design,
> this creates what users call the "AI slop" aesthetic. Avoid this: ...

It does NOT open with "You are a senior frontend engineer with 10 years of experience."
The persona is established by the *failure mode the persona must defeat*. This is much
harder for the model to drift away from than a self-description, because the failure mode
is concrete and observable in the output.

**Our application:** the Drawer's identity opens with the figure-figure failure mode:
"Your figures tend to ship with text that overlaps tick labels, panel titles that get
crashed by neighbor data labels, and bottom-row x-axis labels clipped off the canvas.
This is the single most disqualifying failure mode for a paper figure. Defeat it."

### Pattern B — Few-shot is a vocabulary menu, not paired examples

The frontend-design skill never hands the model a worked input/output pair. Instead it
hands the model **categorical menus** of named options, e.g.:

> Pick an extreme: brutally minimal, maximalist chaos, retro-futuristic, organic/natural,
> luxury/refined, playful/toy-like, editorial/magazine, brutalist/raw, art deco/geometric,
> soft/pastel, industrial/utilitarian, etc.

And the cookbook hands the model **named exemplar sets** per axis:

> Code aesthetic: JetBrains Mono, Fira Code, Space Grotesk
> Editorial: Playfair Display, Crimson Pro, Fraunces
> ...

The exemplar names anchor the latent space without prescribing a single answer. The model
picks one and commits.

**Our application:** instead of (or in addition to) the single reference image, give the
Drawer named exemplar sets per axis:

- *Conference-figure font families:* DejaVu Sans / Helvetica Neue / Arial (NeurIPS/ICML
  body), Times / Computer Modern (Nature body), STIX (Science body), JetBrains Mono /
  Source Code Pro (in-figure code/labels).
- *Conference-figure palette families:* Tableau-10, Seaborn-deep desaturated, ColorBrewer
  Set2 (qualitative), `matplotlib` `viridis`/`plasma` segments (sequential), the
  reference's PIL-sampled palette (always primary).
- *Conference-figure spine treatments:* "left+bottom only, hairline" (NeurIPS), "all four,
  hairline" (Nature), "left+bottom + zero-baseline" (econ).
- *Conference-figure legend treatments:* rounded soft-tinted frame (the reference's
  treatment), no frame at all (Nature body), floating top-of-figure (ICLR comparison
  panels).

The reference is still the primary anchor; the menu is the safety net for elements the
reference can't determine on its own (e.g., extending series count, resolving ambiguous
spine widths under a low-res screenshot).

### Pattern C — Anti-patterns are named, concrete, and paired with a positive INSTEAD

The skill says:

> NEVER use generic AI-generated aesthetics like overused font families (Inter, Roboto,
> Arial, system fonts), cliched color schemes (particularly purple gradients on white
> backgrounds), predictable layouts and component patterns ...

Specific named anti-patterns. Not "avoid bad design."

**Our application:** the Drawer's NEVER list must name our specific failure modes with
the exact matplotlib mechanisms that cause them, and pair each with the INSTEAD:

- NEVER let an annotation text bbox intersect a tick-label text bbox.
  INSTEAD: bump `xytext` until `renderer.get_window_extent()` of the two bboxes is
  disjoint, OR shift the annotation horizontally with `ha='left'`/`'right'`.
- NEVER let the bottom row's `set_xlabel(...)` descender clip the canvas.
  INSTEAD: leave `bottom ≥ 0.14` of figure height and verify with
  `fig.canvas.draw(); ax.xaxis.label.get_window_extent()`.
- NEVER set `xlabel('ε')` on top-row axes when the reference shows it only on the bottom.
  INSTEAD: bottom-row only.
- NEVER set `figsize × dpi == reference pixel dimensions` as a hard rule; the reference's
  effective DPI is unknown. INSTEAD: pick `figsize` to give annotations ≥ 1.5× their
  text-height of headroom above the highest data point, and pick `dpi` independently for
  output sharpness.

---

## 3. The two-persona move (the user's explicit ask)

The user asked for two personas:

1. **画图的 — the Drawer (`figure-illustrator`)**: produces the matplotlib script.
2. **审阅的 — the Reviewer (`figure-critic`)**: audits two axes, with no requirement of
   1-to-1 reproduction of the reference.

The Reviewer's two axes (per the user):
- (a) is the figure close to the reference (style)
- (b) is the figure faithful to the data (does it actually present OUR data correctly)

Plus an absolute *quality floor* the user named explicitly:
- digits/labels must not overlap the X axis (or any other axis element).

Mapping this onto a structured reviewer schema:

```
{
  "verdict": "accept" | "revise" | "reject",
  "quality_floor": {
    "passed": bool,
    "violations": [
      // any of: text_overlaps_tick, text_overlaps_title, label_clipped,
      //        illegible_at_print_size, axis_drawn_off_canvas, ...
    ]
  },
  "axis_data_fidelity": {
    "score_0_to_5": int,
    "findings": [...]
  },
  "axis_style_fidelity": {
    "score_0_to_5": int,
    "findings": [...]
  },
  "top_3_actionable_fixes": [...]
}
```

Decision rule (orchestrator-side, not reviewer-side):

- `quality_floor.passed == false` → ALWAYS `revise`, regardless of the two scores.
- `axis_data_fidelity < 4` → `revise`.
- `axis_style_fidelity < 4` AND budget remains → `revise`.
- Otherwise → `accept`.

This is the structural fix for the v8 root cause #1 ("no quality floor"). The reviewer
cannot ship a 4/10 figure with seven named overlap defects, because *any* overlap defect
trips `quality_floor.passed = false`, which the orchestrator auto-rejects.

---

## 4. The v9 prompt artifacts

- `v9_drawer.md` — the Drawer (illustrator) system prompt. Written in the
  `frontend-design` voice: failure-mode-first identity, named menus per axis, NEVER/INSTEAD
  pairs for the layout invariants, closing pep-talk.
- `v9_reviewer.md` — the Reviewer (critic) system prompt. Two review axes + the explicit
  quality-floor checklist + structured JSON output schema.
- `v9_orchestrator.md` — the loop harness: how the Drawer and the Reviewer talk to each
  other, hard-cap on revisions, what to do when the floor never passes.

These three replace the monolithic v8 prompt in `prompts/v5_prompt.md` (v5–v8 all evolved
within that single-doc form).

---

## 5. Open questions to revisit after the v9 spike runs

- **Is the failure-mode-first identity sufficient to drive the doer to actually run
  `renderer.get_window_extent()` checks?** The v5–v8 prompts mention overlap avoidance
  but do not name the matplotlib mechanism. v9 names it; we should observe whether the
  doer actually invokes it.
- **Does the leaner reviewer schema (§6) actually drive better revisions, or does the
  doer underperform without per-instance fix_hints?** If the doer needs the reviewer to
  spell out matplotlib mechanisms, that's a sign the doer prompt isn't carrying enough
  craft and we should bias the next iteration toward strengthening the doer, not
  re-loading the reviewer with detail.
- **Inspiration vocabulary — is it ever used?** If the doer never references the named
  conference styles (because the reference image is sufficient), the menu is dead weight.
  Watch for that.

---

## 6. Audit noise — borrowing from how code review evolved

> Added after a round of feedback. The first cut of `v9_reviewer.md` had two scored
> axes plus per-finding `fix_hint` strings, and the reviewer routinely emitted ~10
> findings per iter — same volume as v5–v8. The user pointed out that this is the same
> failure mode early AI code review tools went through: lots of comments, low signal,
> developers tune out. They asked us to evolve the reviewer the way code review
> evolved.

### The same anti-pattern, in code review form

The published trajectory of AI code review tooling between roughly 2024 and 2026 is
documented at e.g. [HubSpot Sidekick's 6-month
evolution](https://product.hubspot.com/blog/automated-code-review-the-6-month-evolution),
[Anthropic's `code-review` plugin
prompt](https://github.com/anthropics/claude-code/blob/main/plugins/code-review/commands/code-review.md),
and [Jet Xu's signal-vs-noise
framework](https://jetxu-llm.github.io/posts/low-noise-code-review/). The recurring move:
*stop optimizing for recall; start optimizing for precision*.

Two design moves, in particular, transfer directly:

#### (a) The Judge Agent / "high signal only" gate (HubSpot, Anthropic)

HubSpot's writeup explicitly says: "despite extensive prompt tuning, we couldn't
reliably eliminate low-value feedback this way." Their fix was a second-stage Judge Agent
that filters first-stage findings against three criteria — **succinctness, accuracy,
actionability** — and drops everything that fails. They report 80%+ thumbs-up rate
post-Judge, vs. tune-out behavior pre-Judge.

Anthropic's `code-review` command goes further: a flat ban on flagging anything that
isn't a hard, validated defect:

> CRITICAL: We only want HIGH SIGNAL issues. Flag issues where: the code will fail to
> compile or parse, the code will definitely produce wrong results, clear unambiguous
> CLAUDE.md violations. Do NOT flag: code style or quality concerns, potential issues
> that depend on specific inputs or state, subjective suggestions or improvements. If
> you are not certain an issue is real, do not flag it. False positives erode trust and
> waste reviewer time.

Plus a named "do-not-flag" set that includes "pedantic nitpicks that a senior engineer
would not flag." The senior-engineer filter is the actual policy.

#### (b) Pattern over instance, top-N caps

Tools that score well on the [signal-vs-noise
framework](https://jetxu-llm.github.io/posts/low-noise-code-review/) (Macroscope, BugBot)
do two things consistently: they collapse repeated instances of the same defect into a
single "this pattern is present" finding, and they cap output at a small N rather than
exhaustively listing every site.

### Translating both to vision audit

The vision-audit equivalent of "would a senior engineer block this PR?" is "would a
senior author of a top-conference paper send this figure back to the student?" That's a
much higher bar than "is there anything I could critique here." We want exactly that
bar.

Concretely, the v9 reviewer is being rewritten to:

1. **Use no tools.** Pure vision pass — no PIL, no measurement, no Bash. The reviewer
   reads `reference_clean.png` and `img_iter<N>.png` side by side and judges. (This is
   the user's explicit ask.) Anything that requires measurement is the doer's job, not
   the reviewer's.
2. **Cap themes at ≤5.** Not 10. The hard cap is the policy. If the reviewer feels
   there are more than 5 things, they pick the top 5 *categorically* and fold the rest
   under those headings. (Cap was originally drafted as ≤3, then relaxed to ≤5 per
   user feedback — 3 turned out to be too tight for cases where the figure has both a
   floor problem AND meaningful style notes; 5 still keeps the loop bounded.)
3. **Pattern over instance.** Themes name *categories* — "the layout doesn't reserve
   enough vertical room for the per-point label band" — not pixel locations like "the
   0.04 label is 2 px from the 4 tick." The doer has matplotlib in hand; they will
   resolve the pattern into mechanism.
4. **No prescriptive matplotlib in the reviewer.** The reviewer does not say "set wspace
   to 0.45." That's both a leaky abstraction and (per the user's data-migration point,
   below) often *wrong*: the right wspace for OUR data is not the right wspace for the
   reference's data. The reviewer characterizes; the doer mechanizes.
5. **One coarse fidelity verdict, not two scored axes.** `axis_data_fidelity` and
   `axis_style_fidelity` had the same problem as 0–10 grades in code review: numerical
   scores invite haggling. Replace with one of `{ship, close, off}` plus a ≤100-word
   paragraph. The data axis goes away from the reviewer entirely (see point 1 — no
   tools means no comparing values).

### Why "general" matters: the data-migration case

The user's second framing was about generality: today we are doing 1:1 reproduction, but
the same skill should keep working when our data has a different shape than the
reference's. In that case, *detail-level reviewer hints become actively misleading*. If
the reference has 3 series at moderate density and our data has 7 series at high
density, "match the reference's wspace" is the wrong fix — our layout has different
constraints. A reviewer that traffics in pattern-level themes ("inter-panel spacing is
not absorbing your label density") generalizes; a reviewer that traffics in pixel
prescriptions ("set wspace=0.45") does not.

This is the same reason the doer's NEVER list now includes
`NEVER force figsize × dpi == reference_pixel_dimensions`: the reference is a *style*
anchor, not a *layout-arithmetic* anchor. The reviewer must inherit that same posture.

### What stays detailed: the quality floor

The floor (text overlaps, clipped labels, default-matplotlib look) stays a hard
pass/fail with named kinds because that *is* high-signal — overlap is overlap. But even
there we drop the per-instance locator: "labels overlap the tick row across most
panels" is enough; the doer doesn't need a list of which exact (panel, label) pairs
collide. This is the "pattern over instance" move applied to the floor too.

### The revised reviewer schema (replaces the §3 schema above)

```json
{
  "iter": <int>,
  "quality_floor": {
    "passed": <bool>,
    "violation_kinds": ["text_overlaps_tick", "label_clipped", ...],
    "summary": "<≤1 sentence, pattern-level. null when passed.>"
  },
  "fidelity": {
    "verdict": "ship" | "close" | "off",
    "paragraph": "<≤100 words, characterizes deviation as a category, not as instances>"
  },
  "focus_themes": [
    "<≤1 sentence, imperative, pattern-level. ≤5 entries. Empty list when shipping.>"
  ]
}
```

Decision rule (orchestrator-side, simplified):

- `quality_floor.passed == false` → `revise`.
- `fidelity.verdict == "off"` → `revise`.
- `fidelity.verdict == "close"` AND budget remains → `revise`.
- `fidelity.verdict == "ship"` AND floor passed → `accept`.
