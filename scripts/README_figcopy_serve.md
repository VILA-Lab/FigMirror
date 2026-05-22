# FigMirror serve — local webui for the reference-driven figure loop

`scripts/figcopy_serve.py` is a stdlib-only HTTP server that drives the
FigMirror feedback loop end-to-end from a browser. Two modes:

- **Workspace mode** (`--workspace <dir>`) — multi-run dashboard. Submit
  runs through a paste / drag-drop / file-picker form, watch them
  iterate live, browse the trajectory, refine via chat. Phase 2 ships
  this as the headline path. `MockRunner` is the default backend so
  the UI works without any external dependency.
- **Single-run mode** (positional `<workdir>`) — viewer for a single
  workdir. The skill (or a real backend) writes iter files; this page
  reloads as artifacts land. Phase 1 path; useful when driving codex
  manually from another terminal.

Runs on Python 3.10+. The server itself is stdlib-only; `MockRunner`
also uses `Pillow` for Step 2 chat refinement (overlaying the rcParams
delta on the template image) — see Backends below. Fonts in the webui
are loaded via Google Fonts CDN with a system fallback.

Install deps via `uv`:

```bash
uv sync --group dev   # pulls runtime Pillow plus dev matplotlib/numpy/pytest
```

Then run with `uv run`:

```bash
uv run python3 scripts/figcopy_serve.py --workspace /tmp/workspace
```

The Codex/Claude subprocess runners also launch through
`uv run --project`, so their shell commands inherit the project Python
environment.

## Quickstart — workspace mode (recommended)

```bash
mkdir -p /tmp/figmirror_workspace
python3 scripts/figcopy_serve.py --workspace /tmp/figmirror_workspace
```

Open `http://127.0.0.1:8765/`. The landing page is split:

- **Left panel · New run.** Drop / paste / pick a reference image and
  optional data file, type a style instruction, set `max_iters`, click
  Run. The form posts to `/api/run`, the server stages a workdir, and
  the active backend (default: `MockRunner`) starts producing iter files
  on a 4–8s timer.
- **Right panel · Runs.** Live-polled run-bars (every 3s) showing each
  run's status (`running` / `shipped` / `failed` / `idle`), iter count,
  and a thumbnail of the most recent iter image. Click a run to open
  its trajectory.

### Inputs: paste, drag-drop, or click

- **Reference figure**: focus the dropzone (click anywhere inside it),
  then `Cmd+V` / `Ctrl+V` a screenshot from the OS clipboard. Or drag a
  PNG/JPG file from Finder/Explorer onto the dropzone. Or click for the
  native file picker.
- **Data**: paste a tab-separated table or CSV text, drop a file, or
  click. The page renders only a folded fingerprint (line count, size,
  sha256 prefix, first 3 lines) — no infinite scroll for huge pastes.

### Trajectory page

`/r/<run-name>` shows a single run with a sticky breadcrumb back to the
workspace. Two views, switched via the `[ 01 ] Browse iterations` /
`[ 02 ] Refine via chat` tabs in the page header (`?step=N` query
param):

#### Step 1 — browse iterations

- **Inputs** section is collapsed by default; click to expand the
  reference image + data summary.
- **Iter strip**: horizontal-scroll thumbnails. Cursor proximity scales
  each thumb via a Gaussian falloff (Dock-style magnification).
  `←` `→` keys move focus through the strip; Enter/Space activates;
  Esc collapses the expanded panel.
- **Expanded panel**: click any thumb (or hit Enter on focus) to open
  the iter's expanded view below the strip. Image + reference on the
  left; verdict/floor/anchors/themes/notes/audit on the right; action
  buttons for `★ Select as template` (→ Step 2), `Export code`
  (modal with server-side syntax-highlighted Python + copy button),
  and `Export PDF` (when `figure.pdf` is present).
- URL hash `#iter-N` deep-links to the expanded iter; browser back
  collapses the panel.

#### Step 2 — refine via chat

After picking a template, the chat view anchors on that iter and lets
you refine via NL ("字大一点", "x 轴标题再大些", "switch palette") or via
direct rcParam controls.

Each turn returns:
- An updated image (`refine_NNN.png` written into the workdir).
- A structured `rcparams_delta` (e.g. `{font.size: 13, axes.labelsize: 14}`).
- A short reviewer narrative.

The deltas accumulate into a **Direct controls** panel — each rcParam
the AI mentions surfaces as a number stepper you can tweak directly
(direct-control changes round-trip through `/api/refine` with a
structured payload, so the UI stays in sync). Chat history + accumulated
deltas persist per `(run, template_iter)` in `localStorage`.

`⌘`/`Ctrl` + `Enter` sends the current message.

### Backends

Phase 2 ships three implementations of the `Runner` Protocol:

- **`MockRunner`** (default) — synthesizes plausible iter files into
  the workdir on a timer. One mock serves both real backends because
  the on-disk contract (atomic iter file + `status.json` sidecar
  writes) is identical across them. Pre-staged iter content lives in
  `scripts/figcopy_static/mock_iters/iter{0..4}/`; rebuild via
  `python3 scripts/figcopy_static/mock_iters/_build.py`. Step 2
  refinement uses Pillow to overlay the rcParams delta on the
  template image (annotation card in the bottom-right corner showing
  prev → new arrows) so the visible change reflects what the chat
  said.
- **`CodexRunner`** — Phase 3 stub for `subprocess.Popen(['codex',
  'exec', ...])`. Not wired in Phase 2.
- **`ClaudeRunner`** — Phase 3 stub for the Claude CLI backend. Not
  wired in Phase 2.

The current code defaults to `MockRunner`. Stage G of phase2-webui-
workpanel will land `--mock` / `--codex` / `--claude` CLI flags + the
real CLI invocations.

The two steps spawn the backend differently:

- **Step 1 (the loop)** reuses the existing
  `.codex/skills/figmirror/` skill — the runner just shells
  out to `codex exec` (or `claude`) against that skill. No skill
  changes needed.
- **Step 2 (chat refinement)** is a **separate** subprocess call with
  its own system prompt **baked into the runner module**. The skill
  is not involved. The system prompt instructs the agent to write
  `refine_NNN.png` + `refine_NNN_delta.json` + `refine_NNN_review.txt`
  into the workdir; the runner reads those back as the response.

So real backends ship without any cross-surface change to
`.codex/skills/`.

## Single-run quickstart

For driving the codex skill manually (Phase 1 flow):

```bash
python3 scripts/figcopy_run.py \
    --ref path/to/reference.png \
    --data path/to/data.txt \
    --workdir /tmp/myrun
```

The launcher stages `inputs/` and starts the viewer; you then drive
codex from another terminal:

```
/figmirror  workdir=/tmp/myrun
```

The viewer reloads (≈3s polling) as the skill writes
`figure_iter<N>.py`, `img_iter<N>.png`, `audit_iter<N>.json`.

`Ctrl-C` in the launcher terminal stops the viewer.

The single-run page is missing Step 2 chat refinement (no `/api/refine`
endpoint without a workspace) and the `Select as template` /
`Export code` actions (those route through `/api/runs/<name>/...`
endpoints that only exist in workspace mode). Use workspace mode if
you want the full webui.

## Server endpoints

Workspace mode (`--workspace`):

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Workspace landing (form + live run-bar list) |
| `/api/runs.json` | GET | Workspace state poll target (every 3s) |
| `/api/run` | POST | Stage a run + start runner (multipart upload) |
| `/r/<name>` | GET | Trajectory page; `?step=2&template=N` for Step 2 |
| `/r/<name>/_state.json` | GET | Per-run state poll target |
| `/api/refine` | POST | Step 2 chat / direct-control turn (JSON) |
| `/api/runs/<name>/code/<iter>` | GET | Highlighted Python source (HTML fragment) |
| `/api/runs/<name>/pdf` | GET | Stream `figure.pdf` (Content-Disposition) |
| `/static/<name>/<file>` | GET | Files inside a run's workdir |
| `/static-ui/<file>` | GET | Shared CSS/JS/font assets |

Single-run mode adds `/figcopy_serve.html`, `/_state.json`, and a
single `/<workdir-name>/<file>` static route.

## Flags

| Flag | Default | Purpose |
|---|---|---|
| `<workdir>` (positional) | — | single-run mode: path to a FigMirror workdir |
| `--workspace <dir>` | — | workspace mode: dir holding many runs |
| `--host HOST` | `127.0.0.1` | bind interface |
| `--port N` | `8765` | bind port |
| `--no-open` | off | skip `webbrowser.open` |
| `--no-serve` | off | render HTML and exit (single-run only) |
| `--no-watch` | off | disable live-refresh + lightbox |
| `--upload` | off | push self-contained HTML to a HuggingFace Space |
| `--space user/repo` | `zcahjl3/figcopy-taxonomy-gallery` | HF Space target |

## Non-invasive guarantees

- Single-run mode only **reads** the workdir while serving (other than
  writing the sibling `figcopy_serve.html` on render).
- Workspace mode writes only inside the selected workspace, and only
  through `/api/run` (stage workdir) + `MockRunner` (iter files +
  `status.json` sidecar) + `/api/refine` (refine_NNN.png).
- Does not touch `.codex/skills/figmirror/` or its
  references.
- Tolerates partial workdirs — missing artifacts are surfaced as
  `inputs/X missing` notes rather than crashes.

## Browser support

Targets evergreen Chrome / Firefox / Safari (≥ 14). IE and old Safari
are explicitly out of scope. Phase 2 features that require modern APIs:

- `crypto.subtle.digest('SHA-256')` for the data-fingerprint sha256.
- `DataTransfer.items.add()` for syncing dropped/pasted files into the
  hidden `<input type=file>`.
- `navigator.clipboard.writeText` for the Export Code Copy button.
- `:has()` is *not* used (so older Safari isn't blocked).

## Phase 2 file layout

```
scripts/
├── figcopy_serve.py             # routes + skeleton HTML
├── figcopy_run.py               # single-run launcher
├── figcopy_runner/              # backend-agnostic runners
│   ├── __init__.py
│   ├── interface.py             # Runner Protocol
│   ├── mock.py                  # MockRunner — Phase 2 default
│   ├── codex.py                 # CodexRunner — Phase 3 stub
│   └── claude.py                # ClaudeRunner — Phase 3 stub
└── figcopy_static/              # served at /static-ui/*
    ├── __init__.py              # makes it an importable package
    ├── style.css                # all CSS
    ├── workspace.js             # landing page interactions
    ├── trajectory.js            # trajectory + step 1 + step 2
    ├── highlight.py             # syntax highlighter (NOT served)
    └── mock_iters/              # pre-staged content for MockRunner
        ├── iter0/{img.png, code.py, notes.md, audit.json}
        └── ... (5 dirs total)
```

The static-ui handler filters by extension allowlist
(`.css / .js / image / font / json`) so `.py` files in `figcopy_static/`
(this package's `__init__.py`, `highlight.py`, `mock_iters/*/code.py`)
are unreachable via HTTP.
