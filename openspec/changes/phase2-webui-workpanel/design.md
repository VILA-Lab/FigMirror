# Design — phase2-webui-workpanel

## Context

`figcopy_serve.py` post-frontend-hardening (ac25954) is a 1218-line
single file with HTML/CSS/JS embedded as Python f-strings, a stubbed
"engine" (no codex invocation), and two render paths:
- `render_html()`     — single-run trajectory page
- `render_landing()`  — workspace landing (run list)

This phase reshapes both pages, splits the frontend stack into
real static files, and inserts a `Runner` Protocol so the UI can be
developed against a mock without depending on real codex / claude
spawning.

## Goals / Non-Goals

**Goals:**
- Drag-drop / paste / click for both reference and data inputs.
- Live status on workspace landing.
- Progressive-disclosure trajectory page with Dock-magnification.
- Two-stage flow (Step 1 select template → Step 2 chat refine with
  structured rcParams deltas).
- Frontend stack reorg: vanilla JS, file-split, zero install.
- Mock codex runner so UI dev is unblocked.

**Non-Goals:**
- Real codex spawn — Phase 3.
- Mobile responsive — desktop tool.
- Multi-user concurrency, auth.
- Persistent chat history beyond localStorage.

## File structure (after this change)

```
scripts/
├── figcopy_serve.py              ← thinner: routes + skeleton HTML
├── figcopy_run.py                ← unchanged
├── figcopy_runner/               ← NEW
│   ├── __init__.py
│   ├── interface.py              ← class Runner (Protocol — backend-agnostic)
│   ├── mock.py                   ← MockRunner (active)
│   ├── codex.py                  ← CodexRunner (Phase 3 stub)
│   └── claude.py                 ← ClaudeRunner (Phase 3 stub)
├── figcopy_static/               ← NEW (served at /static-ui/<file>)
│   ├── style.css                 ← all CSS
│   ├── workspace.js              ← landing-page interactions
│   ├── trajectory.js             ← trajectory + step 1 + step 2
│   ├── highlight.py              ← server-side Python tokenizer→span
│   └── mock_iters/               ← pre-staged demo files for Mock
│       ├── iter0/{img.png, code.py, notes.md, audit.json}
│       ├── iter1/...  (5 total, varied verdicts)
│       └── README.md             ← how the mock uses these
└── README_figcopy_serve.md       ← updated
```

## UI architecture

### Workspace landing (/)

```
┌──────────────────────────────────────────────────────────────────┐
│  figcopy · workspace                                              │
│  /datadrive/.../runs                                              │
├──────────────────────────────────────────────────────────────────┤
│  ┌───── New Run ──────────────┐  ┌───── Active Runs (3) ───────┐ │
│  │                              │  │                              │ │
│  │  Reference figure            │  │  ┌──────────────────────┐    │ │
│  │  ┌────────────────────────┐ │  │  │ run-alpha            │    │ │
│  │  │ ⤓ drop / paste / click │ │  │  │ ▰▰▰▰▱  iter 4/6      │    │ │
│  │  │   (preview shows here) │ │  │  │ [final thumb]    →   │    │ │
│  │  └────────────────────────┘ │  │  └──────────────────────┘    │ │
│  │                              │  │  ┌──────────────────────┐    │ │
│  │  Raw data                    │  │  │ run-beta  ✓ shipped  │    │ │
│  │  ┌────────────────────────┐ │  │  │ [final thumb]    →   │    │ │
│  │  │ ⤓ drop / paste / click │ │  │  └──────────────────────┘    │ │
│  │  │ ✓ confirmed: 1247 lines│ │  │  ┌──────────────────────┐    │ │
│  │  │   38 KB · sha:a1b2…    │ │  │  │ run-gamma  ✗ failed  │    │ │
│  │  │   [show full ▾]        │ │  │  │ stopped at iter 2    │    │ │
│  │  └────────────────────────┘ │  │  └──────────────────────┘    │ │
│  │  prompt: ____________        │  │                              │ │
│  │  max iters: [6]              │  │  (poll /api/runs.json q3s)   │ │
│  │  [ Run → ]                   │  │                              │ │
│  └──────────────────────────────┘  └──────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

**Live polling**: `workspace.js` fetches `GET /api/runs.json` every 3s
and re-renders the right panel's run-bar list. Each bar has:
- Run name (link to `/r/<name>`)
- Status pill: `running` / `shipped` / `failed`
- iter counter `i/N` (only when running)
- 80×60 thumbnail of `figure.png` if shipped, else `img_iter<latest>.png`

### Trajectory page (/r/<name>) — Step 1

```
┌──────────────────────────────────────────────────────────────────┐
│  ← Workspace             run-alpha           [Step 1] [Step 2]    │
├──────────────────────────────────────────────────────────────────┤
│   Inputs (collapsed by default)                              ▾    │
│                                                                    │
│   Iterations                                                       │
│   ┌──┐ ┌──┐ ┌──┐ ┌──┐ ┌──┐                                        │
│   │ 0│ │ 1│ │ 2│ │ 3│ │ 4│  ← horizontal scroll, magnify on hover  │
│   └──┘ └──┘ └──┘ └──┘ └──┘    ← / → keys navigate                  │
│                                                                    │
│   ╔══════ click iter 2 ══════════════════════════════════════╗    │
│   ║                                                            ║    │
│   ║  ┌──────────────────┐  ┌─ Iter 2 ────────────────────┐  ║    │
│   ║  │                   │  │ verdict: close               │  ║    │
│   ║  │   big image       │  │ floor: passed                │  ║    │
│   ║  │                   │  │                              │  ║    │
│   ║  │                   │  │ ✓ palette match              │  ║    │
│   ║  └──────────────────┘  │ ✗ tick density too high      │  ║    │
│   ║                         │                              │  ║    │
│   ║                         │ [Select as Template ⭐]      │  ║    │
│   ║                         │ [Export Code] [Export PDF]   │  ║    │
│   ║                         └──────────────────────────────┘  ║    │
│   ╚════════════════════════════════════════════════════════════╝    │
└──────────────────────────────────────────────────────────────────┘
```

**Iter strip implementation:**
- HTML: `<div class="strip">` with N `<button class="thumb" data-iter="…">` children.
- CSS: `transform: scale(var(--s, 1))` on each thumb; transition .15s.
- JS: container `mousemove` listener; for each thumb, compute
  `dx = |cursor.x - thumb.center.x|`; map `dx → scale` via gaussian:
  `scale = 1 + 0.6 * exp(-(dx/120)^2)`; write to `--s` CSS variable.
- Keyboard: ←/→ moves a "focus" thumb (CSS-only :focus styling) and
  scrolls into view.
- Click: deep-link via `location.hash = '#iter-N'`; render expanded
  view inline below the strip (no full reload).
- Browser back: `hashchange` listener collapses expanded view.

### Step 2 — chat refinement (/r/<name>?step=2)

```
┌──────────────────────────────────────────────────────────────────┐
│  ← Workspace                                          [1] [Step 2]│
├──────────────────────────────────────────────────────────────────┤
│  Template: iter 2                                                  │
│  ┌──────────────┐                                                  │
│  │ template img │   Refinement chat                                │
│  └──────────────┘   ┌─────────────────────────────────────────┐    │
│                     │ you: 字大一点                             │    │
│  Direct controls    │ ai:  ✓ updated. font-size 11 → 13.       │    │
│  ─────────────      │      [new image inline]                   │    │
│  font-size  [13]    │ you: x 轴标题再大些                       │    │
│   - +               │ ai:  ✓ updated. axes.labelsize 12→14.     │    │
│  axes.labelsize     │      [new image inline]                   │    │
│  [14]  - +          │                                            │    │
│                     │                                            │    │
│  (icons appear as   │ ┌──────────────────────────────────────┐   │    │
│   the AI mentions   │ │ type your refinement…                │   │    │
│   each rcParam)     │ └──────────────────────────────────────┘   │    │
│                     │ [Send →]                                   │    │
│                     └────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

**API protocol:**

`POST /api/refine`
```json
{ "run": "run-alpha", "template_iter": 2, "message": "字大一点" }
```

Response:
```json
{
  "image_url": "/static/run-alpha/refine_001.png",
  "rcparams_delta": { "font.size": 13, "axes.titlesize": 14 },
  "review": "Increased font.size 11→13 to match the reference's …"
}
```

UI maintains a running map `accumulated_rcparams: {param → value}`
and renders direct controls (number stepper for numeric, dropdown for
enum) as the AI mentions each one. Direct-control changes also POST
to `/api/refine` but with a structured payload instead of NL.

## Server API (full route table)

| Route | Method | Description | Phase |
|---|---|---|---|
| `/` | GET | Workspace landing | mod |
| `/api/runs.json` | GET | All runs status (poll target) | new |
| `/api/run` | POST | Stage a run + start runner | mod |
| `/r/<name>` | GET | Trajectory page | mod |
| `/r/<name>/_state.json` | GET | Per-run state | mod (extend) |
| `/r/<name>/refine_<n>.png` | GET | Refinement output image | served via existing /static |
| `/api/refine` | POST | Step 2 message → new iter | new |
| `/api/runs/<name>/code/<iter>` | GET | Highlighted Python source | new |
| `/api/runs/<name>/pdf` | GET | Final PDF download | new |
| `/static-ui/<file>` | GET | CSS / JS / mock assets | new |
| `/static/<name>/<file>` | GET | Workdir files (existing) | unchanged |

## Runner interface

```python
# scripts/figcopy_runner/interface.py
class Runner(Protocol):
    def start(self, workdir: Path, prompt: str, max_iters: int) -> str:
        """Stage a run; return run_id. Async — returns immediately."""
    def refine(self, workdir: Path, template_iter: int, message: str) -> dict:
        """Step 2 turn. Synchronous — returns {image_url, delta, review}."""
    def status(self, workdir: Path) -> dict:
        """Returns {state: 'running'|'shipped'|'failed', current_iter: int}."""
    def cancel(self, workdir: Path) -> None: ...
```

Concrete impls live in sibling modules (`codex.py`, `claude.py`,
`mock.py`); naming was generalized in Stage C from
`CodexRunner`/`MockCodexRunner`/`RealCodexRunner` once we surfaced
that the abstraction needs to support multiple agent CLIs (codex *and*
claude as Phase 3 backends, plus whatever lands later) — see decision
**D10** at the bottom of this doc.

## Mock implementation strategy

`MockRunner` keeps an in-process registry `dict[workdir, RunState]`
and a thread per run. On `start`:
1. Spawns a daemon thread.
2. Thread copies `figcopy_static/mock_iters/iter<N>/*` files into
   `workdir`, renaming `iter` placeholder → `iter<N>` to match the
   skill's filename convention.
3. Sleeps 4–8s between iters to simulate real codex latency.
4. Updates state per iter; writes `status.json` for fallback consumers.
5. After 5 iters (or fewer if a mock-iter has `verdict=ship`), stops
   and copies `iter<final>/img.png` → `figure.png`.

On `refine`: pattern-match the user message against a small
dictionary:
```
"字大" / "字小" / "bigger" → font.size ±2
"x 轴" / "x axis"        → axes.labelsize / axes.titlesize
"配色" / "palette"       → cycle through 3 pre-rendered palette variants
…
```
For unmatched messages, return a generic acknowledgement + the same
image. The point is to **drive the UI**, not to be smart.

## Decisions

### D1 — Vanilla JS, no framework

User wanted zero-install. Ruled out Alpine.js (CDN dep is acceptable
philosophically but unnecessary at this complexity), React/Svelte
(needs build pipeline). Vanilla works for everything we need.

### D2 — Server-side Python syntax highlighter

~80 lines: a tokenizer + classifier producing `<span class="kw">`,
`<span class="str">`, etc. CSS in `style.css`. Avoids 50KB
highlight.js download; we only have one language to highlight.

### D3 — Mock codex via pre-staged demo files + threading

Keeps UI dev decoupled from codex orchestration concerns. Phase 3
swaps the mock body for `subprocess.Popen` while preserving the
runner interface.

### D4 — Status: runner-authoritative; mtime as fallback

When `MockRunner` is active, status comes from in-memory state
+ `status.json`. For hand-staged workdirs (no runner attached), fall
back to mtime heuristic (mtime of newest iter file < 30s ago →
"running"). Documented as a fallback, not the primary path.

### D5 — Step 2 returns structured rcParams delta

Captures the user's vision: NL → image **+ params**, so the UI can
gradually surface direct controls. Mock returns hand-curated deltas
via a pattern-match dictionary (8 keyword groups → fixed delta +
review string).

**Phase 3 contract — corrected during apply** (was originally framed
as a SKILL.md change; that was wrong):

Step 1 (the figure-style-copier loop) and Step 2 (chat refinement)
are **separate invocations**. Step 1 runs the
``.codex/skills/figure-style-copier/`` skill via codex / claude;
Step 2 is its own ``subprocess.Popen([backend, …])`` call with a
custom **system prompt baked into the runner code** (e.g.
``CodexRunner.refine`` writes the prompt at call time). The system
prompt defines the output protocol — "save the new figure to
``refine_NNN.png``, write the delta as ``refine_NNN_delta.json``,
write the review text into ``refine_NNN_review.txt``" — so the
``.codex/skills/figure-style-copier/`` artifacts stay untouched (D9
holds across phase 3).

Phase 3 work for Step 2 is therefore self-contained inside
``figcopy_runner/codex.py`` + ``claude.py``: build the system
prompt, spawn the subprocess, parse the sidecar files. No
cross-surface change. No markdown reviews — plain text is fine
since the webui treats it as a single string.

### D6 — Horizontal scroll for ALL iters; visual capacity 5

Per user. With `max_iters` up to 20, all-at-once would drown the user.

### D7 — Dock-magnification animation

Per user. ~30 lines vanilla JS (mousemove handler + gaussian
distance kernel + scale transform via CSS variable).

### D8 — Folded data confirmation, not full display

Per user: user just wants confirmation "the data went in". Render
sha256 + line count + first 3 lines. `[show full ▾]` to expand.

### D9 — Do NOT touch `.codex/skills/figure-style-copier/`

Per user, cross-surface scope was rejected for this phase.

### D10 — Generalize Runner naming (Stage C apply-time refinement)

Original proposal named the abstraction `CodexRunner` and the impls
`MockCodexRunner` / `RealCodexRunner`. During Stage C apply, the user
flagged that the abstraction needs to support both codex AND claude
as real Phase 3 backends (plus whatever agent CLIs land later), and
the mock isn't codex-specific — one mock serves any backend. So:

- `CodexRunner` (Protocol)  →  `Runner` (Protocol, in `interface.py`)
- `MockCodexRunner`         →  `MockRunner` (in `mock.py`)
- `RealCodexRunner`         →  split into:
                               - `CodexRunner` (in `codex.py`, real codex)
                               - `ClaudeRunner` (in `claude.py`, real claude)

Why two real-backend stubs instead of one shared `RealRunner`: each
backend has a different CLI surface (flags, prompt format, log shape),
so the spawn-and-parse logic differs per backend. The mock + Protocol
shape is shared; the concrete spawn glue is not.

## Risks

- **Mock realism gap**: if mock iters look very different from real
  codex output, we may design UI that breaks under real data. Mitigation:
  build mock_iters/ from `figcopy_serve_demo.py`'s synthetic outputs
  (which are calibrated to look like real loop output).
- **Threading correctness**: MockRunner uses daemon threads.
  Server already uses ThreadingMixIn (post-ac25954), so concurrent
  reads of workdir during write are possible. Mitigation: each iter's
  files are written atomically (write to `.tmp`, rename), so the
  poll-disk loop never observes a half-written file.
- **Hash route → expanded view interaction**: easy to get wrong.
  Browser back button must collapse the expanded view, not navigate
  away from the run.
- **Step 2 control panel growth**: if AI mentions 30+ rcParams over a
  long chat, the controls panel becomes unusable. Defer collapsing /
  grouping to Phase 3; for Phase 2 we'll cap at first 8 most-recent
  params.
