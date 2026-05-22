# Tasks — phase0-style-transfer-loop

> Status as of 2026-05-06. Stage 1 spike complete; pending = stress tests +
> Stage 2 work.

## Done — Stage 1 (style transfer loop)

- [x] Phase 0 spike: 6 manual loop runs (v8 → v11-rerun-2)
- [x] Doer / Reviewer separation pattern landed (Decision 8)
- [x] Anchor-based preservation against monotonic drift (Decision 9)
- [x] L1 / L2 / L3 grounding hierarchy (Decision 10)
- [x] Aesthetic library v11.7 — 12 patches, 3 meta-principles, 12 property
       sections (Decisions 11–12)
- [x] Reviewer JSON schema + bounded-PIL subprocess (Decision 13)
- [x] Decision rule: ship/close/off + floor independence (Decision 14)
- [x] Damping rule: no opposite-direction themes (Decision 15)
- [x] Per-property reliability annotations are conditional (Decision 16)
- [x] Self-correcting validation: fresh-agent rerun (v11-rerun-2)
       reproduces 0 of 5 originally-named bugs
- [x] First natural ship verdict (v11-rerun-2 iter 4)
- [x] Product positioning principles articulated (P1–P5)
- [x] v11 prompt set consolidated as canonical asset
       (`resources/prompts/figure-style-copier.md`)
- [x] Spike heavy outputs gitignored; prompt-iteration history tracked
       under `spike-results/prompts/`

## Pending — Stage 1 polish (stress tests)

These will likely surface NEW library dimensions we haven't covered. The
expectation is each test → 1-3 new library patches → re-validate. Not all
will be done at once; user delivers reference images one at a time.

- [ ] **Bar chart stress test** — chart-type generalization. Library is currently
       line-plot-biased; expect new sections for `Bar geometry` (bar width,
       gap, value-label position).
- [ ] **Data inconsistency stress test** — data shape ≠ reference shape (different
       x-range, y-range, series count, trend direction). Validates the "reference
       is STYLE anchor, not LAYOUT anchor" rule operationally.
- [ ] **Extra-series stress test** — data has more series than reference (e.g.
       3-line ref → 5- or 7-line target). Validates the L2 palette extension
       menu (Tableau-10 / Seaborn-deep / ColorBrewer Set2).
- [ ] **Std-band reference stress test** — reference uses confidence shading
       (mean ± std). Library has no `Statistical band` element-type section yet;
       expect to add one.
- [ ] **Single → grid promotion stress test** — reference is single-panel,
       target needs N-panel grid. Library currently has no single→grid
       promotion guidance; user has flagged this as a must-have capability.
- [ ] **Dirty-data echo step (Decision 7)** — the v8 → v11-rerun-2 spike used
       pre-clean numeric inputs and never exercised the data-echo / misparse
       escape hatch. Per `proposal.md`, dirty terminal-pasted data is a core
       Stage 1 entry path. Stage 1 cannot honestly be called complete without
       a spike on a representative dirty-paste input. Targets Decision 7's
       echo confirmation flow.

## Pending — Stage 2 (user-driven detail tweak)

Stage 2 was specified in proposal.md but not built or spiked. The loop only
exercises Stage 1 (style transfer). Stage 2 needs:

- [ ] Stage 2 prompt set design (what's in scope: tick scale, font size,
       legend wording, etc.)
- [ ] Stage 1 → Stage 2 transition protocol (per Decision 2 — user explicit gate)
- [ ] Stage 2 spike (driven by real user requests on a Stage-1-shipped figure)

## Pending — packaging

- [ ] Skill packaging: `.claude/skills/figure-style-copier/SKILL.md`
       (consolidates v11 prompts + product positioning + invocation pattern)
- [ ] Codex variant evaluation (separate change; placeholder)
- [ ] Phase 1 scope — TBD; depends on what Stage 2 spike surfaces

## Won't do — out of envelope per product positioning P1, P2

Marked deliberately, NOT pending:

- [-] Low-resolution reference robustness (P1: user contract is
       "paper-screenshot at normal-reading size")
- [-] Adversarial / aesthetically-bad reference handling (P3: we are
       opinionated; user can ask to relax, framework does not auto-relax)
- [-] Cross-figure framework state for multi-figure consistency (P4: handled
       by serial-chain workflow + transitivity; not internal state)
- [-] Stress-test matrix as a separate maintained file (per session
       agreement: patches go directly to library, lessons go to design.md)
- [-] Hand-drawn / infographic / pie-chart reference handling (P5: out of
       paper-figure envelope by definition)

## How to read this file

When picking up next session:

1. Find the next unchecked `[ ]` under **Pending — Stage 1 polish**.
2. Confirm with user (which test image to run).
3. Run the spike per `resources/prompts/figure-style-copier.md` orchestrator
   pattern (`claude -p --model opus` reviewer subprocess).
4. After the spike, patch the library if new dimensions emerge.
5. Update this file: check the box, add lessons to `design.md` if
   structurally significant.
6. Append session entry to `openspec/sessions/`.
