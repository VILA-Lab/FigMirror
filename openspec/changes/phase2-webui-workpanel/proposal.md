# Phase 2 — figcopy webui WorkPanel redesign

> Builds on phase 1's navigation shell + new-run form (commits 8e846d4,
> 17c8f52, ceaf5a9) and the frontend-hardening pass (607aa79, ac25954).
> Phase 0 (`.codex/skills/figure-style-copier/`) is **untouched** in this
> phase; this is webui-only.

## Why

Phase 1 shipped a functional but visually generic webui that exposes raw
codex orchestrator artifacts (per-iter cards, JSON dumps, code listings)
all at once. Real-world use surfaces three pain points:

1. **Input friction.** Form requires file picker for both reference and
   data; can't paste a screenshot from clipboard or drag-drop. Long
   pasted data takes infinite vertical space in the trajectory view.
2. **Workspace isn't a workspace.** Landing page lists runs but doesn't
   show live status (running vs done, current iter, final preview),
   making it useless as a multi-run dashboard. No way to navigate back
   from a run page.
3. **No actual interaction.** Engine is "stubbed" — submitting the form
   only stages files; user must manually run codex in another terminal
   and re-load the page to see iters. The two-stage style-transfer →
   refinement flow that phase 0 SKILL.md describes has zero UI surface.

Phase 2 closes all three gaps and brings the webui to feature parity
with the codex skill's design intent.

## What Changes

### UI redesign (visual + interaction)

- **Workspace landing** becomes a WorkPanel: left "New run" panel
  accepts reference and raw data via clipboard paste, drag-drop, or
  file-picker; previews the reference and **fingerprints** the data
  ("✓ 1247 lines · 38 KB · sha:a1b2") instead of dumping it. Right
  "Active runs" panel lists each run as a long bar with live status
  (running / iter N / shipped) and a final-figure thumbnail.
- **Trajectory page (Step 1)** uses progressive disclosure: first view
  is a horizontal iter strip (visual capacity 5, all iters scrollable),
  with macOS-Dock-style cursor-proximity magnification. Click an iter
  → expanded view: large image left; audit metadata + "Select as
  Template" + "Export Code" (modal with syntax-highlighted Python) +
  "Export PDF" (direct download) right.
- **Step 2 chat** activates after the user picks a template. Each NL
  message ("字大一点") returns a new image plus a **structured
  rcParams delta**. UI accumulates the deltas into icon/slider
  controls so the user can manipulate the same parameters directly on
  later turns instead of re-typing.
- **Sticky breadcrumb** on every non-landing page (`← Workspace`).

### Frontend stack reorg

- Pull JS and CSS out of `figcopy_serve.py` into static files under
  `scripts/figcopy_static/`. Python serves them via a new
  `/static-ui/<file>` route. **Remains stdlib-only, zero install,
  zero build step.**
- Server-side Python syntax-highlighter (~80 lines) — no third-party
  highlighter; we only need to highlight the one language we emit.

### Mock backend orchestration

- New abstraction `Runner` (Protocol) with three implementations:
  - **`MockRunner`** — synthesizes plausible iter files into the
    workdir on a timer to drive the UI. Used during Phase 2 dev.
    One mock serves all real backends (the contract — write iter
    files + status sidecar — is identical across them).
  - **`CodexRunner`** — `subprocess.Popen(["codex", "exec", ...])`
    spawning real codex sessions. Stub now; wired in Phase 3.
  - **`ClaudeRunner`** — `subprocess.Popen(["claude", ...])` for the
    Claude CLI backend. Stub now; wired in Phase 3.
- `figcopy_serve.py` exposes `POST /api/run` and `POST /api/refine`
  (Step 2 chat); both delegate to the runner. The runner writes files;
  the existing poll-disk loop renders them.
- "Running" status comes from the runner ("is the subprocess alive +
  what iter is it on") with **mtime heuristic as fallback** (for
  hand-staged workdirs without a runner attached).

## Capabilities

### New Capabilities

- `figcopy-webui-workpanel`: Multi-run WorkPanel-style workspace
  supporting paste / drag-drop input, live status of in-flight runs,
  two-stage trajectory exploration (Step 1 progressive-disclosure +
  Step 2 chat refinement with structured-parameter feedback).

### Modified Capabilities

- `figcopy-webui-singlerun` (existing): trajectory page redesign;
  removes default-open audit JSON dump; adds Step 1 horizontal-scroll
  iter strip + Step 2 chat panel.

## Impact

- `scripts/figcopy_serve.py` — refactored: HTML rendering becomes
  thinner (skeleton only), heavy UI logic moves to static JS.
- `scripts/figcopy_static/` — **new directory**: `style.css`,
  `workspace.js`, `trajectory.js`, `highlight.css`, plus pre-staged
  `mock_iters/` with realistic synthetic iter files.
- `scripts/figcopy_runner/` — **new module**: `Runner` Protocol +
  `MockRunner` (active) + `CodexRunner` / `ClaudeRunner` (Phase 3 stubs).
- `.codex/skills/figure-style-copier/` — **untouched** (per user
  direction).
- `scripts/README_figcopy_serve.md` — updated with new flags / endpoints.

## Out of scope (deferred to Phase 3)

- Real subprocess invocation for `CodexRunner` + `ClaudeRunner`
  (both stubs; Phase 3 wires them + handles process lifecycle,
  stdout capture, cancel).
  - Step 1 (the figure-style-copier loop) goes through the existing
    `.codex/skills/figure-style-copier/` skill — no SKILL.md change.
  - Step 2 (chat refinement) is a **separate** subprocess call with
    a custom system prompt baked into the runner code. The runner
    writes the prompt at call time; the skill is not involved in
    Step 2 at all. So **no cross-surface change to .codex/skills/
    is required** for either step.
- Multi-tenant / shared workspace concerns.
- Step 2 chat persistence across sessions (in-memory + localStorage
  only in Phase 2).
- Authentication. Local-only assumption holds.
- Mobile / responsive layout below 880px.

## Two-step delivery rhythm (per user direction)

1. **Stage A–F**: build UI against the **mock** runner. After each
   substantial milestone, run server on `:7860` and hand off to user
   for visual review (port-forwarded locally).
2. **Stage G** (separate change or end of this one): swap mock for
   real codex spawn once UI is signed off.

The proposal as written includes Stage G in scope but it's hidden behind
the runner interface — flipping mock → real should be a small commit at
the end.
