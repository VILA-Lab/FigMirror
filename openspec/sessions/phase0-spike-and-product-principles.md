# Session: phase 0 spike + library evolution + product principles

> Date: 2026-05-06
> Driver: project owner
> Co-driver: Claude Sonnet 4.5
> Branch: `agent-paper-figure-generator`
> Predecessor: Inherited from a prior agent's v8 spike (commit `00c934c`)
> Successor: TBD — next session picks up from `tasks.md` pending list

---

## What this session was

A long, deeply iterative co-design session. Started with the user handing me
a v8 spike result that had specific overlap defects and asking me to "continue
where the prior agent left off." Ended with a working v11 prompt set
(consolidated as `resources/prompts/figure-style-copier.md`) validated by a
fresh-agent rerun that achieved the first natural `ship` verdict.

Along the way the design grew **conceptually** (not just incrementally):
single-agent loop → doer/reviewer pair → anchor-based preservation against
drift → L1/L2/L3 grounding hierarchy → aesthetic library as L2 living document
→ 3 cross-cutting meta-principles → product positioning principles.

## Spike trajectory (what was actually built)

| Version | Key add | What it fixed | Where it failed |
|---------|---------|---------------|-----------------|
| v8 (inherited) | First overlap-floor + audit-via-Bash subprocess | — | 7 named overlap defects shipped at iter2 |
| v9 | Doer/Reviewer two-persona + identity-from-failure-mode + few-shot worked examples | Floor passed cleanly | Monotonic drift (aspect 1.95 → 1.55, spines L+B → all-4) |
| v10 | `anchor.what_is_right` + bounded PIL for reviewer + damping rule | Drift stopped | Spine color #dcdcdc bug (mean-of-strip on thin lines) |
| v11 | L1/L2/L3 hierarchy + aesthetic library as L2 + per-property reliability table | Spine color, V-gridlines, type voice all corrected | Loose inter-panel spacing, loose legend density, font ID wrong (sans when ref is serif) |
| v11.1–v11.4 | Library patches for direction, spacing classes, compactness meta, hairline calibration | Each patch surfaced by user observation | — |
| v11.5 (Measurement humility meta) | Per-panel aspect → eyeball + iterate (NOT PIL-lock) | Solved per-panel false-precision | — |
| v11.6 (Type overhaul + aspect coupling) | Serif vs sans cue table + classic font menu + figure vs per-panel aspect distinction | Font correctly identified | hspace=0.55 anti-pattern flagged |
| v11-rerun (validation) | Fresh agent reads patched library | 0/5 originally-named bugs reproduced | iter1 audit subprocess infra hiccup |
| v11-rerun-2 (validation) | Fresh agent + 3 meta-principles + L1-perceived/PIL distinction | First natural `ship` at iter 4 with library citation by name | dual hallucination on V gridlines (doer disabled, reviewer affirmed) |
| v11.6 patches 9–12 | PIL verification artifact requirements + symmetric anti-patterns + reviewer due diligence | dual-hallucination mitigation; gridline color middle-of-class | — |

The dominant pattern: **user observation → diagnose missing library dimension
→ add dimension (not just value) → re-run → next observation**. 12 library
patches in this session. Prompt main bodies (drawer/reviewer/orchestrator)
unchanged after v11.0; all evolution was in the library.

## The framework that emerged (5 layers)

1. **Doer (`figure-illustrator`)** — produces the matplotlib script, runs floor
   self-check, owns all detail-level work.
2. **Reviewer (`figure-critic`)** — fresh-context Bash subprocess, bounded PIL,
   strict JSON output. Never sees the data file or source code, only the two
   PNGs + library + prior audit.
3. **Orchestrator** — the doer's session, acts as harness. Decision rule is
   ship/close/off + floor independence.
4. **Aesthetic library (L2)** — living document of paper-figure conventions.
   Each section: classes + ranges + dependencies + PIL reliability + L1/L2
   precedence. Now 3 meta-principles + 12 property sections + ~14 patches.
5. **Anchor preservation** — reviewer outputs `anchor.what_is_right[]` with L1/L2
   prefixes; orchestrator forwards as hard preserve list to doer next iter.

## The 3 meta-principles (the actual core of v11)

1. **Compactness preference** — top-conference figures are tight, not airy.
   Default tight class. Don't fall back to mpl defaults.
2. **Hairline calibration** — fine elements visible AND recessive. Pick literal
   middle of L2 ranges, not extremes (both pale and dark extremes are wrong).
3. **Measurement humility** — code measurement is heuristic-conditional, not
   categorical. False precision is worse than acknowledged uncertainty. The
   human workflow is `constrain → perceive → render → adjust`.

## The 5 product positioning principles (NEW this session — were previously verbal-only)

1. **P1. User contract on input quality** — paper-figure screenshot at
   normal-reading size. Not engineering for tiny low-res inputs.
2. **P2. The 80/20 envelope** — optimize for the median user, not adversarial.
3. **P3. Opinionated stylist, not faithful copyist** — we override Compactness
   defaults and floor violations even when reference has them. Taste is part
   of the product.
4. **P4. Multi-figure consistency via serial chain** — transitivity gets us
   80% mutual consistency for free. Series-extension non-determinism handled
   by user-side serial workflow ("draw 4-line first, use as next reference").
5. **P5. Stress-test envelope is mid-to-high paper figures, not adversarial** —
   bar/line/scatter/std-band/grid in scope; hand-drawn/infographic/pie out of
   scope.

These are now committed to `design.md § Product positioning principles`.

## Decisions made about workflow / process

- **Library evolves via patches, not rewrites.** Every spike-surfaced bug → add
  a dimension to a library section, do not rewrite. 12 patches in this session.
- **Prompt main bodies stable, library is the living layer.** v9 → v11.7
  changed library 12 times, drawer/reviewer/orchestrator zero times after v11.0.
- **No stress-test matrix maintained as a separate file.** Patches go directly
  to library; structurally-significant lessons go to design.md.
- **Use `openspec/sessions/<topic>.md` for session-continuity** (this file
  is the first one, established at end of session per user's request).
- **Spike heavy outputs (claude-code-*/, codex-*/) are gitignored.** Only
  the prompts subdir + prompt-iteration history is tracked.

## Open conversations (not resolved this session)

- **Stage 2 scope** — proposal.md describes Stage 2 as user-driven detail tweak,
  but it's not built. When does it kick in vs. when do we re-enter Stage 1?
  Will surface organically when we exercise the loop on real user flows.
- **When to package as skill** — `.claude/skills/figure-style-copier/SKILL.md`
  is the eventual destination per proposal.md, but premature now. Wait until
  stress tests + Stage 2 are exercised.
- **Phase 1 contents** — phase 1 wasn't named explicitly. User mentioned
  prioritizing "stage 1 first" without specifying what stage 1 of phase 1
  contains. Probably becomes clear after a couple stress test sessions.

## Where to look (file path index)

| What | Where |
|------|-------|
| Canonical v11 prompt set (single file) | `resources/prompts/figure-style-copier.md` |
| Phase 0 design (post-spike framework + product principles) | `openspec/changes/phase0-style-transfer-loop/design.md` |
| Phase 0 task tracker | `openspec/changes/phase0-style-transfer-loop/tasks.md` |
| Per-version prompt history (v9, v10, v11) | `spike-results/prompts/v*_*.md` |
| v9 → v10 design notes | `spike-results/prompts/v9_findings.md`, `v10_design_notes.md` |
| Per-spike outcomes (selection.md, process.md, audits, figures) | `spike-results/claude-code-subagent-*/`, `spike-results/codex-subagent-*/` — **gitignored** per `.gitignore` (heavy ephemeral run outputs). NOT in fresh checkout. To recover, re-run the relevant spike using `resources/prompts/figure-style-copier.md` orchestrator pattern; design rationale lives in tracked v9_findings + v10_design_notes + this session journal. |

## Next session expected actions

1. User delivers a reference image (one of: bar chart / data inconsistent /
   extra series / std band / single→grid).
2. Run the spike per `resources/prompts/figure-style-copier.md` orchestrator
   pattern; Bash subprocess for reviewer.
3. When new library dimensions surface, patch library; check the
   corresponding box in `tasks.md`.
4. If structurally significant lesson, add to `design.md`.
5. Append a new session journal entry under `openspec/sessions/`.

## A note about the journey (more reflective)

The biggest insight from this session was **the failure mode of meta-principles
being over-generalized**. Twice, a meta-principle that fixed one class of bug
caused a new class:

1. `Hairline calibration: don't pick pale extreme` → agent overshot to dark
   extreme. Fix: write anti-patterns symmetrically.
2. `Measurement humility: prefer eyeball over BRITTLE measurement` → agent
   generalized to "prefer eyeball over ALL measurement" and skipped reliable PIL.
   Fix: explicit per-property reliability + HARD RULE wording for cases where
   PIL is required.

The deeper pattern: **giving an agent a "when not to do X" rule, the agent
tends to over-generalize to "don't do X" full stop**. Solve via:
(a) symmetric framing of anti-patterns,
(b) verification artifacts (PIL output quoted in notes is non-fakable; eyeball
    claims are),
(c) per-property explicit boundaries on when the meta-principle applies.

Worth carrying forward into any future skill / library design.
