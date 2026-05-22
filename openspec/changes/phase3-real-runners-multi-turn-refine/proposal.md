# Phase 3 — Real runner backend + multi-baseline multi-turn refine

> Builds on phase 2 (`#9` 120f43f — WorkPanel + iter strip + Step 2 chat
> UI) and the phase-3-stub scaffolding it left in
> `scripts/figcopy_runner/{claude,codex}.py`. Closes the last gap: the
> webui is currently wired to a mock all the way through `/api/refine`.
> This phase makes the backend real, adds **multi-baseline multi-turn
> chat refinement** in Phase 2, and adds **streaming progress to the
> browser via SSE** — all while preserving the project's zero-runtime-
> dependency posture (`pyproject.toml` still ships `dependencies = []`).

## Why

Phase 2 shipped a complete UI surface (WorkPanel, iter strip, Step 2
chat textarea + direct rcParams steppers, localStorage chat history)
but everything below `/api/refine` is **string pattern-matching in
`MockRunner.refine()`** — type "字大" and you get a canned
`{font.size: 13}` overlay rendered by PIL. The two real backends
(`CodexRunner`, `ClaudeRunner`) raise `NotImplementedError` on every
method. Five problems block the product from being usable past the
demo loop:

1. **Refine is fake.** A user can submit a real ref + data and watch
   the mock iters play back, but the moment they try to refine
   ("make the legend smaller"), they get plausible-looking nonsense
   produced by a hardcoded pattern table — the agent never saw their
   figure.

2. **Refine is single-turn.** Even if `/api/refine` were real, the
   current contract is one-shot per request: the runner has no
   session state, and the chat history lives only in `localStorage`
   on the client. A second turn ("smaller still, and make the labels
   black") would have no context for "still" or what "the labels"
   referred to.

3. **Refine is single-baseline.** Step 2 today is "pick exactly one
   iter as template, then refine it." Real prompt-engineering flow
   is more like Diffusion's reference-image flow: user picks **a set
   of iters** as a combined prompt — "take iter 1's layout, iter 3's
   colors, iter 5's legend placement" — and refines the combination.
   The current "select as template" UI doesn't allow multi-select,
   and the API only accepts a single `template_iter`.

4. **Loop progress isn't streamed.** Step 1 iters take 30+ minutes
   total. The current 3-second `_state.json` poll is fine for "is it
   still going", but a user watching tool-by-tool agent activity
   ("now reading drawer.md", "now writing img_iter3.png") would get
   a much higher-fidelity sense of progress with token-level
   streaming. Same goes for refine turns, which can run 30 s to a
   few minutes.

5. **The Runner Protocol is incomplete.** `interface.py` defines
   `start / status / cancel` but **not `refine`** — yet
   `figcopy_serve.py:1428` calls `runner.refine(...)`. Mock happens
   to implement it; the contract isn't codified.

## What Changes

### Project posture (the most important "change")

- **Zero new runtime dependencies.** `pyproject.toml` keeps
  `dependencies = []`. The server stays on stdlib `http.server` +
  `socketserver.ThreadingMixIn` (current architecture). No FastAPI,
  no Starlette, no aiohttp, no SDK, no ACP — all evaluated and
  documented in design.md.
- **Install-and-run experience preserved**: `uv sync` plus one
  `python scripts/figcopy_serve.py --workspace <dir>` is all a new
  user needs. Drag a reference image into the page, type a prompt,
  watch iters appear. The phase-3 changes are additive — no
  ceremony introduced.

### Runner backend — real subprocess wiring

- **`CodexRunner.start()` and `CodexRunner.refine()`** invoke the
  real `codex` CLI via `subprocess.Popen` (`codex exec --json …`
  for first turn; `codex exec resume <session-id> --json …` for
  follow-up turns). The runner captures session-id from the first
  JSON event, persists it to disk, and re-uses it for subsequent
  turns on the same `(run, baseline_set)`.
- **`ClaudeRunner.start()` and `ClaudeRunner.refine()`** invoke the
  real `claude` CLI via `subprocess.Popen` (`claude -p
  --output-format stream-json --verbose --include-partial-messages
  --resume <session-id>` for follow-ups). Symmetric to codex.
- **Step 1 reuses existing skills**:
  `.codex/skills/figure-style-copier/` for codex,
  `.claude/skills/figure-style-copier/` for claude. `codex` actually
  has a documented skill system (`.codex/skills/<name>/SKILL.md` is
  the official convention, OpenAI's own repo uses it — earlier
  phase-2 stub docstrings implying otherwise are mistaken; design.md
  §clarifications). Skills are unchanged.
- **Step 2 (refine) does NOT use a skill** by design — refine is
  one-shot from the **API** perspective (one request, one response),
  but the agent is allowed and expected to **retry internally**
  (write code → run matplotlib → inspect → fix → rerun) until the
  png renders correctly. The server only sees a `refine_complete`
  event when both `refine_NNN.png` and `refine_NNN.json` exist on
  disk. No skill is invoked. The system prompt is built **inline
  in the runner module** at call time from `(baseline iters'
  artifacts + accumulated rcparams snapshot + compressed history
  of recent refines on this run + user message)`.
- **Subprocess lifecycle**: each runner registers `pid` in an
  in-process session registry keyed by `(workdir, slot, set_id?)`;
  `cancel(workdir, ...)` sends `SIGTERM`, escalates to `SIGKILL`
  after 3 s grace.
- **Atomic file writes preserved** — `.tmp` + rename for everything
  (`status.json`, iter files, `refine_NNN.{png,json}`, `chat.jsonl`
  appends, `sessions.json`).

### Multi-baseline refine — `baseline_iters: List[int]`

- **`/api/refine` signature changes**: replaces `template_iter: int`
  with `baseline_iters: List[int]` (1-or-more, sorted). The agent
  receives **all** baseline images and their Python sources as
  prompt context; the user's natural-language message ("take the
  first one's layout and the third's colors") does the rest.
- **`set_id` is a content hash**: `set_id = sha1(",".join(str(i)
  for i in sorted(baseline_iters)))[:8]`. Selecting `[1,3,5]`
  always maps to the same `set_id`, so reopening Phase 2 with the
  same multiselect resumes the same chat session. Selecting a
  different set (e.g. adding iter 7) → new `set_id` → new chat.
- **Multiple concurrent chats per run**: a single run can have many
  Phase 2 chats, each at its own `set_id`. Backend supports this
  natively (each session is independent on disk + at the agent
  level); frontend exposes a chat list UI (deferred polish — Phase 3
  ships with one active chat surfaced at a time, but the server
  schema is multi-chat-ready).
- **System prompt carries compressed history of prior refines on
  the run** (last 3 OR first+last, whichever is more informative):
  each entry is the `refine_NNN.py` code + `refine_NNN.json`
  (delta + review). The agent's own chat transcript is **not**
  included. This is what lets a brand-new chat session understand
  "字大了" even when the font was bumped in a different earlier
  chat (design.md §D12).
- **No hard cap on baseline count**. Typical usage is 2–3
  baselines (documented in README as guidance); the server does
  not enforce an upper limit (design.md §D14).
- **Set is locked once a chat starts**: changing the baseline set
  mid-conversation is not supported; the user starts a new chat
  (new `set_id`) instead. Simpler model; agent's "the first
  reference" indexing stays unambiguous.

### Multi-turn refine — server-side session

- **`/api/refine` becomes real**: still POST JSON `{run,
  baseline_iters, message?, adjustments?}`. The endpoint computes
  `set_id`, dispatches to `runner.refine(...)`, which blocks until
  the agent writes `refine_NNN.png` + `refine_NNN.json`, then
  returns `{image_url, rcparams_delta, review, set_id, seq}`.
- **Chat history on disk**: `{workdir}/chat.jsonl`, append-only,
  one line per turn (user message + assistant reply). Each line
  carries its `set_id` so the file holds all of a run's Phase-2
  conversations, filtered by `set_id` on read.
- **Session-id per `(run, set_id)`** stored in
  `{workdir}/sessions.json` as `{iter: <sid>, refine: {<set_id>:
  <sid>}}`. Re-opening the same baseline set resumes the agent's
  context via `--resume <sid>`.
- **`adjustments` direct controls become a prompt engineering shim**:
  the existing "rcParams stepper" UI continues to work, but the
  endpoint translates `{adjustments: {font.size: 15}}` into a
  natural-language message (`"Adjust: font.size = 15"`) server-side
  before handing it to the agent. No structured adjustments path
  to the agent — the agent only sees prose. Server's prompt
  engineering keeps the agent's behavior consistent regardless of
  whether the user typed or clicked.

### Streaming — SSE + `Last-Event-ID`

- **New endpoint `GET /api/runs/<name>/chat/<set_id>/stream`** —
  Server-Sent Events stream of normalized agent events. Each event
  has `id: <seq>`, `event: <type>`, `data: <json>`.
- **Server → browser event union** (normalized across codex and
  claude):
  - `text` — assistant streaming text delta
  - `tool_call_start` / `tool_call_end` — agent invoked a tool
  - `turn_start` / `turn_end` (with `status: completed | failed |
    cancelled`)
  - `refine_complete` (figcopy-specific: emits the final
    `{image_url, rcparams_delta, review}` payload)
  - `iter_complete` (figcopy-specific: Step-1 loop progress)
- **Per-session ring buffer** (in-memory, bounded ~1000 events) +
  per-session subscriber queue list. Server keeps the buffer alive
  across browser disconnects; reconnect with `Last-Event-ID: <seq>`
  replays from that point.
- **Step 1 also gets SSE**: `GET /api/runs/<name>/iter/stream`
  emits the same event types for the iter loop, so the UI can show
  live tool activity instead of polling.
- **Stdlib-only SSE** — written against `http.server` + threads
  (~80 LOC including event bus). No `sse-starlette`, no async
  framework. The existing `ThreadingMixIn` model handles 5–6
  concurrent long-lived SSE connections trivially (each takes one
  thread; we are not at the scale where this matters).

### New endpoints

| Method + Path | Purpose |
|---|---|
| `POST /api/refine` | Submit a refine turn (changed: multi-baseline) |
| `GET /api/runs/<name>/chat/<set_id>` | Fetch chat.jsonl filtered to set_id (rehydration on page load) |
| `GET /api/runs/<name>/chat/<set_id>/stream` | SSE: events for one Phase-2 chat |
| `GET /api/runs/<name>/iter/stream` | SSE: events for Step-1 iter loop |
| `GET /api/runs/<name>/chats` | List all chats this run has (each = a `set_id` with baseline list + turn count) |
| `POST /api/runs/<name>/cancel?slot=iter` | Cancel the Step-1 loop |
| `POST /api/runs/<name>/cancel?slot=refine&set_id=<id>` | Cancel an in-flight refine turn |

### Runner Protocol — codify `refine`

- **Add `refine(workdir, *, baseline_iters, message=None,
  adjustments=None) -> dict`** to the Protocol in `interface.py`.
  Return: `{"image_url": str, "rcparams_delta": dict, "review":
  str, "set_id": str, "seq": int}`.
- **Document the multi-turn + multi-baseline contract** in the
  interface docstring: successive `refine()` calls on the same
  `(workdir, baseline_iters)` MUST share an agent session;
  different `baseline_iters` MUST get distinct sessions.
- **`MockRunner.refine()`** updated to honor the new signature.

### Backend selection

- `--backend {mock,codex,claude}` flag, default `codex`.
- The legacy `--mock` flag becomes a deprecated alias for `--backend
  mock` (warns, still works).
- Codex and Claude are **equally supported first-class** — defaults
  to codex purely on the experience-tested basis (more in design.md
  §D3).

## Capabilities

### New Capabilities

- `figcopy-real-runner-backend`: Real subprocess-driven runner
  backends (`CodexRunner`, `ClaudeRunner`) that invoke the `codex`
  and `claude` CLIs for both Step 1 (iter loop, via existing skills)
  and Step 2 (multi-baseline multi-turn refine, via inline system
  prompts), normalize their stream-json outputs into a shared event
  union, manage per-`(run, set_id)` agent sessions, and honor a
  `cancel()` contract via SIGTERM/SIGKILL.

### Modified Capabilities

- `figcopy-webui-workpanel` (existing): Step 2 chat replaces
  single-template with multi-baseline (List[int]) selection;
  `/api/refine` becomes a real backend dispatch; chat history
  persisted server-side and indexed by content-hash `set_id`; SSE
  endpoints for live event streaming added; cancel button surfaced
  in the UI.

## Impact

- `pyproject.toml` — **unchanged** (`dependencies = []`).
- `scripts/figcopy_runner/interface.py` — Protocol gains `refine`;
  docstring documents multi-baseline + multi-turn contract.
- `scripts/figcopy_runner/codex.py` — full implementation replaces
  the phase-3 stub (subprocess lifecycle, JSONL event parsing into
  normalized events, session-id persistence, system-prompt
  construction, refine output parsing).
- `scripts/figcopy_runner/claude.py` — symmetric to codex.py.
- `scripts/figcopy_runner/mock.py` — `refine()` signature aligned;
  same `chat.jsonl` / `sessions.json` writes as real runners
  (exercises the same code paths in CI / offline dev).
- `scripts/figcopy_runner/event_bus.py` — **new**: in-memory
  per-session ring buffer + subscriber queues; ~80 LOC stdlib.
- `scripts/figcopy_runner/chat_log.py` — **new**: shared
  atomic-append helper for `chat.jsonl`.
- `scripts/figcopy_runner/refine_prompt.py` — **new**: shared
  builder for the inline Step-2 system prompt (used by both real
  runners; mock can ignore it).
- `scripts/figcopy_runner/adjustments_to_prose.py` — **new**:
  prompt-engineering shim that turns `{font.size: 15}` into
  `"Adjust: font.size = 15"`.
- `scripts/figcopy_serve.py` — `/api/refine` reworked (multi-
  baseline, real dispatch); new endpoints (`/chat/<set_id>`,
  `/chat/<set_id>/stream`, `/iter/stream`, `/chats`, `/cancel`);
  default backend flips from `mock` to `codex`; pre-flight check
  for CLI availability on PATH.
- `scripts/figcopy_static/trajectory.js` — multi-select baseline
  UI (checkboxes on iter strip); reads chat list per set_id;
  rehydrates chat from server on load; opens an SSE connection
  instead of polling `_state.json`; Cancel button. **Phase 2 chat
  mode** adds a right-panel "current working figure" that
  live-updates on `refine_complete` SSE events (the user watches the
  png change as the agent finishes each turn); clicking the working
  figure reveals the **previous** `refine_<N-1>.png` so the user can
  see what changed turn-over-turn (design.md §D15). This replaces
  the Phase-1 "click iter to see reference" gesture for the Phase-2
  context.
- `scripts/figcopy_static/workspace.js` — also switches from
  polling to SSE (`/iter/stream`) for live run-bar updates.
- `scripts/README_figcopy_serve.md` — documents new endpoints,
  `--backend` flag, prerequisites (`codex` and `claude` on PATH).
- **`.codex/skills/figure-style-copier/`** and
  **`.claude/skills/figure-style-copier/`** — **untouched**.
- **External**: requires `codex` CLI (1.0+) and/or `claude` CLI on
  PATH on the server host. Documented in README.

## Out of scope (deferred to Phase 4+)

- **WebSocket** transport for a single-multiplexed-connection
  model. SSE is sufficient for our 5–6-session scale. Migration
  path documented in design.md §D2.
- **Cross-host session storage** (Redis / S3 `SessionStore`).
  Local-disk JSONL is the source of truth; no multi-host deploy
  goal.
- **Authentication / multi-tenant.** Local-only.
- **Frontend chat-list UI** (showing multiple chats per run as
  tabs). Server schema supports it; UI surfaces one chat at a
  time in Phase 3 to ship faster.
- **Interactive permission prompts** ("Claude wants to run `Bash:
  rm …`, allow?"). Not needed for refine; would require WebSocket
  uplink.
- **`fork_session=True`** for deterministic iter re-rolls.
- **Mobile / responsive layout.**
