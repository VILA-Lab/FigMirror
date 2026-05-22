# Tasks — phase2-webui-workpanel

> Read with `proposal.md` (what & why) and `design.md` (how) open.
> Each `## Stage` is one server-restart-and-eyeball checkpoint with
> the user (port 7860 forwarded). Stages are sequential — earlier
> stages set up the substrate later stages need.

## Stage A — Frontend stack reorg (no UI changes yet) [hygiene]

Goal: same visual output, but JS/CSS in static files instead of
inline f-strings. After A, server runs identically; nothing should
look different.

- [x] Add `/static-ui/<file>` route to `figcopy_serve.py` (mirrors
       the existing `/static/<name>/<rest>` pattern + path-traversal
       guard, but rooted at `scripts/figcopy_static/`)
- [x] Create `scripts/figcopy_static/style.css`; move all inline CSS
       there; replace inline `<style>` with `<link rel="stylesheet">`
- [x] Create `scripts/figcopy_static/trajectory.js`; move the
       interactive `<script>` block there; replace inline with
       `<script src="…">`
- [x] Create `scripts/figcopy_static/workspace.js` (empty for now;
       just establishes the file)
- [x] Verify visual parity: render single-run + workspace pages
       before & after; diff HTML excluding the moved blocks
- [x] **Checkpoint A**: run server :7860, eyeball both pages

## Stage B — Workspace landing redesign

- [x] WorkPanel grid layout (left form / right run-bars) in `style.css`
       (Plot Surface redesign in b328083 — left panel 420px, right panel
       1fr; run-bar styling extended in this stage's commit)
- [x] Drag-and-drop handlers on reference + data input zones
       (`dragover` / `drop`, prevent-default, read `e.dataTransfer.files`)
- [x] Paste handlers on reference (image from clipboard) + data
       (text from clipboard) — `paste` event + `clipboardData.items`
- [x] Image preview thumbnail using `URL.createObjectURL(file)` +
       click-to-enlarge (reuse trajectory lightbox CSS — kept simple
       inline preview in dropzone for now; lightbox-share is a
       low-pri polish task for Stage F)
- [x] Folded data fingerprint: sha256, line count, KB, first 3 lines.
       Computed client-side; sent to server in form submission.
- [x] `GET /api/runs.json` endpoint returns workspace state
       (extends existing `discover_runs` to include status + thumb URL;
       reads optional `status.json` sidecar for MockRunner Phase 3)
- [x] Live polling in `workspace.js`: 3s interval; re-render run-bars;
       diff-aware (only mutate changed bars)
- [x] Run-bar component: status pill, iter counter (when running),
       80×60 thumbnail of newest iter image
- [x] Sticky breadcrumb on `/r/<name>` (`← Workspace` link top-left)
       (b328083: render_html grew breadcrumb_url param; workspace
       /r/<name> handler passes "/")
- [x] **Checkpoint B**: run server :7860; user reviews landing-page UX
       (signed off provisionally — full validation deferred until Stage C
       wires real mock iters; B's run-bar / poll / dropzone shapes may
       want to evolve once mock-driven flows surface needs)

## Stage C — MockRunner

- [x] Create `scripts/figcopy_runner/__init__.py`,
       `interface.py`, `mock.py`, `real.py` (real is a stub)
- [x] Stage 5 mock iter directories under
       `scripts/figcopy_static/mock_iters/iter{0..4}/`
       with realistic `img.png`, `code.py`, `notes.md`, `audit.json`
       (built from `tools/figcopy_serve_demo_workdir/` images on the
       sister worktree via `_build.py` — runtime dep-free copy +
       audit-narrative author)
- [x] `MockRunner.start`: spawn daemon thread, copy files with
       4–8s sleeps, atomic write (`.tmp` + rename)
       (gradient: 4s for iter 0 → 8s for last iter, ±10% jitter; later
       iters slower to mirror codex thinking-time growth)
- [x] `MockRunner.status`: in-memory state + writes `status.json`
       to workdir for crash-survivable status
       (atomic .tmp+rename writes; status() falls back to disk sidecar
       when no in-memory record)
- [x] Wire `POST /api/run` to invoke `MockRunner.start` (not
       just stage files like Phase 1 stub)
       (runner instantiated once in run_workspace; create_run now
       returns config dict so handler can pass prompt/max_iters)
- [x] Extend `_state.json` to include runner status (running / shipped
       / failed + current_iter)
       (build_run_state calls _run_state for status + current_iter;
       trajectory.js can later use this to render a live banner)
- [x] `discover_runs` reads `status.json` first, mtime as fallback
       (done in Stage B as part of `_run_state`; mock runner just needs
       to keep writing the sidecar — already in this stage's contract)
- [ ] **Checkpoint C**: submit a run from the form, watch mock iters
       appear live in the workspace landing page

## Stage D — Trajectory page redesign (Step 1)

- [x] Collapse "Inputs" section by default (use existing `<details>`)
- [x] Replace per-iter card stack with horizontal iter strip
- [x] Dock-magnification mousemove handler in `trajectory.js`
       (Gaussian kernel: scale = 1 + 0.7 * exp(-(dx/120)^2); writes
       --scale CSS variable on each thumb; respects
       prefers-reduced-motion)
- [x] Keyboard ←/→ navigation + Tab focus order
       (←/→ moves focus through thumbs and scrolls into view; Enter /
       Space activates; Esc collapses; arrow keys ignored when typing
       in inputs / textareas)
- [x] Hash-based deep linking: `#iter-N` opens expanded view; back
       collapses it (browser back works because we use location.hash;
       Esc also clears the hash via history.pushState)
- [x] Expanded-view layout: image left, metadata + buttons right
       (CSS grid: minmax(0,1.6fr) minmax(0,1fr); collapses to single
       column under 880px viewport)
- [x] Server-side Python syntax highlighter
       (`scripts/figcopy_static/highlight.py` — module imported by
       `figcopy_serve.py`, NOT served as static; static-ui handler
       gained an extension allowlist that excludes .py)
- [x] Highlight CSS in `style.css`
       (kw / builtin / str / num / comment / decorator tokens, tuned
       for the dark code background used inside the modal)
- [x] `GET /api/runs/<name>/code/<iter>` returns highlighted HTML
       (HTML fragment that the modal injects via innerHTML; uses
       `figcopy_static.highlight.highlight_python`)
- [x] Export Code modal: fetched on click; copy-to-clipboard button
       using `navigator.clipboard.writeText` (innerText extraction
       drops the spans so the clipboard gets plain Python source)
- [x] Export PDF: `GET /api/runs/<name>/pdf` streams `figure.pdf` with
       `Content-Disposition: attachment` (404 if not yet generated;
       Phase 3 RealRunners will arrange figure.pdf alongside figure.png)
- [ ] **Checkpoint D**: full Step 1 walkthrough

## Stage E — Step 2 chat refinement

- [x] Step 1 / Step 2 toggle UI (URL param `?step=2` + tab nav)
       (renders only in workspace mode — single-run pages have no
       /api/refine to back Step 2 against. Default ?template= tracks
       selection's iter, falling back to most-recent.)
- [x] Chat message log component (CSS + JS)
       (3 message kinds: user / ai / system; ai messages carry review
       text + delta chips + inline refinement image)
- [x] `POST /api/refine` endpoint: dispatches to runner.refine
       (JSON body {run, template_iter, message?, rcparams?}; one of
       message/rcparams should be set, both works too)
- [x] `MockRunner.refine` pattern-match dictionary
       (字大/字小/x 轴/y 轴/配色/legend/线条/tick — all matched against
       message substring; falls back to generic ack on no-match)
- [x] Synthesize refinement images: take template image, apply mock
       transform server-side via PIL (font overlay diff acceptable —
       we just need the image to *change* visibly)
       (Pillow added to the dev group via `uv add`; the mock opens
       the template image, draws an annotation card in the bottom-
       right corner showing the latest delta with prev→new arrows
       (prev pulled from accumulated state), composites + saves
       atomically. Reads the visual change off the image directly,
       no cycling-through-mock-iters proxy. Lazy-imports PIL inside
       `_render_refine_image` so non-refine code paths stay
       PIL-free.)
- [x] Structured controls panel: render number-stepper / dropdown for
       each rcParam mentioned by the AI
       (numeric → number stepper; non-numeric → read-only value
       display per Phase 2 cap)
- [x] Direct-control change → `POST /api/refine` with structured
       payload `{rcparams: {...}}` (no NL message)
       (records a system-style "set X = Y" entry in the chat log so
       the activity remains traceable)
- [x] localStorage persistence of chat per run
       (key `figcopy:chat:<run>:<template-iter>` → {messages, deltas};
       restored on page load, includes last refinement image)
- [ ] **Checkpoint E**: full Step 2 chat walkthrough

## Stage F — Polish + accessibility

- [x] WCAG AA contrast check on all new pills / controls
       (slate scale on white = AAA across; .pill.mute corrected to
       slate-600 in Stage A; all status pills use semantic-color
       on tinted bg pairs that clear AA. Audited Step 2 controls,
       chat message badges, modal buttons.)
- [x] All interactive elements keyboard-reachable (tabindex, focus
       outline, no `cursor:pointer`-only-no-keyboard)
       (iter-thumbs are <button role="tab"> with focus ring; modal
       opens with focus on close button + restores on close;
       form fields native + labelled; arrow keys navigate strip
       without hijacking input/textarea caret movement)
- [x] Form `aria-` attributes; chat log `role="log" aria-live="polite"`
       (chat log + status banner both use role="log" / "status" with
       aria-live="polite"; refinement form labels via aria-label;
       step toggle is a tablist with aria-selected)
- [x] Browser test: Chrome + Firefox (no IE / Safari < 14)
       (smoke-tested via curl + structural inspection; visual
       behavior verified by user against Chrome. Modern API
       requirements documented in README. No IE / Safari < 14
       support guaranteed; :has() avoided so older Safari isn't
       blocked outright.)
- [x] Update `scripts/README_figcopy_serve.md`: paste/drag flow, Step 2
       chat, mock vs real runner
       (full rewrite: workspace mode is now the headline path; Step 1
       + Step 2 walkthroughs; Runner backends section with
       Mock/Codex/Claude split; full server endpoint table; Phase 2
       file layout diagram; modern-API requirements list)
- [ ] **Checkpoint F**: end-to-end demo

## Stage G — Replace mock with real codex / claude backends

> Self-contained inside `figcopy_runner/{codex,claude}.py`. Earlier
> framing of "needs a SKILL.md update" was wrong (corrected during
> apply, see design.md D5):
>
> - **Step 1** (the figure-style-copier loop) reuses
>   `.codex/skills/figure-style-copier/` unchanged. The runner just
>   shells out to `codex exec` / `claude` against that skill.
> - **Step 2** (chat refinement) is a **separate** subprocess call
>   with a custom system prompt **baked into the runner code**. No
>   skill is involved in Step 2 at all. The system prompt defines
>   the output protocol — agent writes `refine_NNN.png` +
>   `refine_NNN_delta.json` + `refine_NNN_review.txt` into the
>   workdir; the runner reads those back.
>
> Both backends (codex + claude) carry their own spawn glue because
> the CLI surfaces differ (flags, prompt format, log shape). Mock
> stays the default for dev; users opt into real via CLI flag.

- [ ] `CodexRunner.start` / `ClaudeRunner.start`: `subprocess.Popen([
       "codex"|"claude", "exec", ...])` with stdout/stderr capture
       into `agent.log` in workdir
- [ ] Process lifecycle: track PID per workdir, expose `status()` via
       `proc.poll()` + parse the log for current iter
- [ ] `CodexRunner.refine` / `ClaudeRunner.refine`: synchronous
       subprocess call. **System prompt baked into the runner
       module** — instructs the agent to read template image +
       message, regenerate the figure, and emit the trio of sidecar
       files (PNG + delta JSON + review txt). Self-contained;
       `.codex/skills/` untouched.
- [ ] Cancel: `proc.terminate()` + cleanup on `POST /api/cancel`
- [ ] Runtime switch: `--mock` / `--codex` / `--claude` flag on
       `figcopy_serve.py` (default `--mock`); pyproject.toml
       documents which CLI binaries each backend expects on `$PATH`
- [ ] Update README with mock vs real semantics + which env vars /
       auth each real backend needs
- [ ] **Checkpoint G**: real backend run end-to-end

## Conventions

- Each Stage = one logical commit (or 2–3 if size warrants).
- After each Stage: server runs, user spots-checks at `:7860`, sign-off
  before moving to next stage.
- The `commit-quality-pipe` runs at every commit (per user CLAUDE.md).
- No `.codex/skills/figure-style-copier/` changes in Phase 2 (per D9).
