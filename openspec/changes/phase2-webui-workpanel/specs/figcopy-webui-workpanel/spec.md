# figcopy-webui-workpanel

> Detailed product behavior of the Phase 2 webui. See proposal.md for
> "why" and design.md for "how".

## ADDED Requirements

### Requirement: Workspace landing as WorkPanel

The workspace landing page SHALL render as a two-column WorkPanel:
left = New Run input form, right = Active Runs list with live status.

#### Scenario: Multiple runs in flight

- **GIVEN** a workspace with 3 in-progress runs (one running, one
  shipped, one failed)
- **WHEN** the user opens `/`
- **THEN** the right panel SHALL show 3 long-bar cards, each with the
  run name, a status pill (`running` / `shipped` / `failed`), an iter
  counter for the running one (e.g. `iter 4/6`), and a thumbnail of
  the most recent iter image (or `figure.png` if shipped).
- **AND** the panel SHALL re-poll `/api/runs.json` every 3 seconds
  and update only the run-bars whose state has changed.

#### Scenario: Empty workspace

- **GIVEN** a fresh workspace with zero runs
- **WHEN** the user opens `/`
- **THEN** the right panel SHALL show an empty-state message; the
  left panel SHALL be fully functional for creating the first run.

### Requirement: Reference and data inputs accept paste, drag-drop, and file-picker

The New Run form SHALL accept three input modes for both reference
image and raw data: clipboard paste, drag-and-drop, click-to-pick.

#### Scenario: Clipboard paste of screenshot

- **GIVEN** the user has copied a paper-figure screenshot to the OS
  clipboard
- **WHEN** the user focuses the reference input zone and presses
  Ctrl+V (or Cmd+V on macOS)
- **THEN** the image SHALL be captured client-side, an inline
  thumbnail SHALL appear, and the form's hidden file input SHALL be
  populated so submission carries the image as a multipart upload.

#### Scenario: Drag-drop of data file

- **GIVEN** the user drags a `.csv` file onto the data input zone
- **WHEN** the file is dropped
- **THEN** the file SHALL be read client-side, its sha256 / line count
  / size SHALL be displayed as a fingerprint summary, and the file
  SHALL be carried in the form submission.

### Requirement: Data input shows folded fingerprint, not full content

The data input zone SHALL display a folded summary (line count,
size, sha256 prefix, first 3 lines) rather than the full pasted
content.

#### Scenario: User pastes 5000-line table

- **GIVEN** the user pastes a 5000-line tab-separated table
- **WHEN** paste completes
- **THEN** the input zone SHALL show only the fingerprint summary
  + 3 sample lines + a "show full ▾" disclosure trigger.
- **AND** the input zone height SHALL NOT grow proportionally to the
  pasted length.

### Requirement: Trajectory page shows Step 1 horizontal iter strip with progressive disclosure

The per-run trajectory page SHALL show all completed iters as a
horizontal-scroll strip with a visual capacity of 5 thumbnails;
detailed review information SHALL be hidden until the user clicks an
iter.

#### Scenario: User opens a run with 8 iters

- **GIVEN** a run with 8 completed iters (none yet selected)
- **WHEN** the user opens `/r/<name>`
- **THEN** the page SHALL show 5 iter thumbnails in view, with the
  strip horizontally scrollable to reveal the remaining 3.
- **AND** the page SHALL expand the selected iter, or the latest iter
  when none is selected, so generated vs reference is immediately
  visible.
- **AND** detailed audit JSON, drawer notes, and code SHALL remain
  behind explicit disclosure or export actions on initial load.

#### Scenario: Cursor proximity magnification

- **GIVEN** the iter strip is visible
- **WHEN** the user moves the mouse horizontally across the strip
- **THEN** each thumbnail's scale SHALL be a smooth function of cursor
  proximity to its center, peaking at the thumbnail under the cursor
  and falling off symmetrically on both sides.

#### Scenario: Click an iter to expand

- **GIVEN** the iter strip is visible
- **WHEN** the user clicks an iter thumbnail
- **THEN** an expanded view SHALL render below the strip showing the
  iter's image (large), audit metadata (verdict, floor, anchor list),
  and action buttons: `Select as Template`, `Export Code`, `Export PDF`.
- **AND** the URL hash SHALL update to `#iter-N` so the browser back
  button collapses the expanded view.

### Requirement: Step 2 chat refinement returns structured rcParams deltas

After the user selects a template, the page SHALL switch to a chat
view in which each user message produces a new image plus a
structured rcParams delta; the UI SHALL surface the accumulated
rcParams as direct controls.

#### Scenario: First refinement turn

- **GIVEN** the user has selected iter 2 as template
- **WHEN** the user types "字大一点" and submits
- **THEN** the server SHALL return `{image_url, rcparams_delta:
  {font.size: 13}, review: "…"}`.
- **AND** the chat log SHALL show the user message, the AI response
  with the new image inline, and a controls panel SHALL render a
  `font.size` numeric stepper initialized to 13.

#### Scenario: Direct-control adjustment

- **GIVEN** a controls panel with `font.size: 13` rendered after a
  prior NL turn
- **WHEN** the user clicks the stepper to set `font.size: 15`
- **THEN** the page SHALL POST to `/api/refine` with a structured
  payload `{rcparams: {font.size: 15}}` (no NL message).
- **AND** the chat log SHALL show a "set font.size = 15" entry plus
  the new image.

### Requirement: Sticky breadcrumb navigation

Every non-landing page SHALL include a sticky `← Workspace` link in
the page header.

#### Scenario: Navigate from run page back to workspace

- **GIVEN** the user is on `/r/<name>`
- **WHEN** the user clicks `← Workspace`
- **THEN** the browser SHALL navigate to `/` and display the
  WorkPanel landing.

### Requirement: Frontend stack is vanilla JS with file-split static assets

CSS and JS SHALL live in separate static files served from
`scripts/figcopy_static/`. The webui SHALL require no `npm install`,
no build step, and no third-party JS dependencies (CDN or otherwise).

#### Scenario: Cold install from clone

- **GIVEN** a fresh clone of the repo
- **WHEN** the user runs `python3 scripts/figcopy_serve.py --workspace /tmp/ws`
- **THEN** the server SHALL start successfully without any prior
  install steps beyond having Python 3.10+ (Pillow, used only by the
  MockRunner refine path, comes from `uv sync --group dev`; the page
  works without it on every flow except `/api/refine`).
- **AND** the page SHALL render correctly even when external network
  requests fail or are blocked. Fonts are loaded from Google Fonts CDN
  to satisfy the distinctive-typography requirement (Plus Jakarta Sans
  + JetBrains Mono); a system fallback stack — `-apple-system,
  BlinkMacSystemFont, 'Segoe UI', Roboto, …` — kicks in if the CDN is
  unreachable, so the layout / interactions remain functional.
  No JS, no third-party CSS framework, no external image / icon /
  build-tool dep beyond fonts.

### Requirement: MockRunner is the default backend in Phase 2

The webui SHALL ship with a `MockRunner` that synthesizes plausible
iter files. `CodexRunner` and `ClaudeRunner` stubs SHALL exist with
the same `Runner` Protocol interface but are not wired up by default
in Phase 2.

#### Scenario: Submit a run via the form

- **GIVEN** the user has filled the New Run form with reference + data
- **WHEN** the user clicks `Run`
- **THEN** `MockRunner.start` SHALL be invoked.
- **AND** mock iter files SHALL appear in the workdir at 4–8 second
  intervals, visible via the workspace landing page's live polling
  and via the trajectory page once the user opens it.

### Requirement: Code export uses server-side Python syntax highlighting

The "Export Code" action SHALL show the iter's Python source in a
modal with syntax highlighting performed server-side (no third-party
JS highlighter).

#### Scenario: Export code from selected iter

- **GIVEN** the user has expanded iter 3 on the trajectory page
- **WHEN** the user clicks `Export Code`
- **THEN** the page SHALL fetch `/api/runs/<name>/code/3` and display
  the response (highlighted HTML) in a modal with a copy-to-clipboard
  button.
