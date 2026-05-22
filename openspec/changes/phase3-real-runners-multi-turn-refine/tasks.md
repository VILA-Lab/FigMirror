# Tasks — phase3-real-runners-multi-turn-refine

> Read with `proposal.md` (what & why) and `design.md` (how) open.
> Stages are sequential. Each `## Stage` ends with a "Checkpoint"
> where the change can be eyeballed / smoke-tested. Stage A lands
> the substrate (no user-visible change), B/C wire each real
> backend, D/E build SSE + UI, F flips the default and polishes.

## Stage A — Substrate (protocol, event bus, helpers) — no user-visible change

Goal: codify everything that mock + real runners share. Mock keeps
working identically; new code paths are exercised by mock too so
B/C aren't writing into a vacuum.

- [x] A.1 Add `refine(workdir, *, baseline_iters: list[int],
       message=None, adjustments=None) -> dict` to the `Runner`
       Protocol in `scripts/figcopy_runner/interface.py`; document
       the multi-baseline + multi-turn contract in the docstring
       (per design.md §D5/D6/D7).
- [x] A.2 Add `sessions.json` schema helpers to `interface.py`:
       `read_sessions(workdir) -> dict` + `write_sessions(workdir,
       data)` (atomic `.tmp` + rename, mirroring
       `read_status_sidecar`).
- [x] A.3 Add `compute_set_id(baseline_iters) -> str` to
       `interface.py` (SHA-1[:8] of sorted-comma-joined-deduped;
       per design.md §D7).
- [x] A.4 Create `scripts/figcopy_runner/chat_log.py` with
       `append_turn(workdir, *, role, content, set_id,
       baseline_iters, **extras)` — atomic file rewrite
       (`.tmp` + rename of full file; chat.jsonl is small enough)
       + `read_turns(workdir, set_id=None) -> list[dict]` for
       filtering by set_id.
- [x] A.5 Create `scripts/figcopy_runner/events.py` defining the
       `SessionEvent` discriminated union from design.md §D5
       (`TextEvent`, `ToolCallStartEvent`, `ToolCallEndEvent`,
       `TurnStartEvent`, `TurnEndEvent`, `IterCompleteEvent`,
       `RefineCompleteEvent`). Use `@dataclass` + `Literal` type
       tags, **stdlib only**.
- [x] A.6 Create `scripts/figcopy_runner/event_bus.py` with the
       `EventBus` class (per design.md §D5 + backend spec
       "In-memory event bus"): `publish`, `subscribe`,
       `unsubscribe`, `replay`, per-session ring buffer (cap=1000),
       monotonic seq per session_key. Thread-safe (`threading.Lock`
       around the per-session state). All `queue.Queue`-based.
- [x] A.7 Create `scripts/figcopy_runner/refine_prompt.py` with
       `build_system_prompt(workdir, baseline_iters,
       accumulated_rcparams) -> str` — collects:
       (a) each baseline iter's PNG path + Python source,
       (b) accumulated rcparams snapshot,
       (c) recent refine history per design.md §D12 / spec
           "Turn-1 system prompt includes recent refine history"
           (helper: `select_history_entries(workdir) ->
           list[Path]` returning the chosen `refine_NNN.py`/`.json`
           pairs — last 3 if ≤3 prior, else first + last 2),
       (d) the output contract instructing the agent to write
           `refine_NNN.png` + `refine_NNN.json` AND retry
           internally until png renders correctly (per spec
           "Agent retries rendering internally"),
       (e) the rule that the agent SHALL NOT emit a "done"
           signal until both files land atomically.
       Function MUST be deterministic — identical inputs produce
       identical output (testable via golden string).
- [x] A.8 Create `scripts/figcopy_runner/adjustments_to_prose.py`
       with `to_prose(adjustments: dict, message: str | None) ->
       str` translating e.g. `{font.size: 15}` to `"Adjust:
       font.size = 15"` (combined with message if present).
- [x] A.9 Create `scripts/figcopy_runner/_lifecycle.py` with the
       shared subprocess registry (module-level dict keyed by
       `(str(workdir), slot)`) and shared cancel implementation
       (SIGTERM → 3 s → SIGKILL).
- [x] A.10 Update `scripts/figcopy_runner/mock.py:refine(...)`
       signature to match the new Protocol; use `compute_set_id`,
       `chat_log.append_turn`, and `write_sessions` so the mock
       backend exercises every new helper. Existing pattern-match
       table can stay (still useful for offline dev).
- [x] A.11 Update `scripts/figcopy_serve.py:_route_refine` to
       accept `baseline_iters: list[int]` from the request body,
       compute `set_id`, and pass everything as kwargs to
       `runner.refine`. **Backward-compat shim**: if the request
       has `template_iter: int` (legacy), translate to
       `baseline_iters: [template_iter]` (log a deprecation
       note); remove after Stage F.
- [x] A.12 Unit tests under `tests/figcopy_runner/`:
       - `test_compute_set_id.py`: order-independence, dedup
         behavior, deterministic.
       - `test_event_bus.py`: publish, subscribe, replay, ring
         truncation surfaces `history_truncated`, monotonic seq
         under concurrent publishers.
       - `test_chat_log.py`: append + filter-by-set_id, atomic
         (rename only after write).
       - `test_adjustments_to_prose.py`: pure-function input →
         output.
       - `test_refine_prompt.py`: golden-string tests for
         `build_system_prompt` covering: 0 prior refines (no
         history section), 2 prior refines (both included), 7
         prior refines (first + last-2 selected). Determinism
         check via repeated calls.
- [x] **Checkpoint A**: `pytest tests/` green; smoke against
       `--backend mock` shows the chat working exactly like phase 2
       AND `chat.jsonl` + `sessions.json` are appearing in workdirs
       with the new shapes.

## Stage B — CodexRunner real subprocess wiring

Goal: `--backend codex` works end-to-end for both Step 1 and Step 2.
Default still `mock` (flips in Stage F).

- [x] B.1 In `scripts/figcopy_runner/codex.py`, implement
       `CodexRunner.start(workdir, prompt, max_iters)`:
       spawn `codex exec --json [--sandbox workspace-write] ...`
       via `subprocess.Popen`, cwd containing
       `.codex/skills/figure-style-copier/`. Stdout reader thread
       parses JSONL events.
- [x] B.2 Inside the reader thread: translate codex's JSONL event
       schema to our `SessionEvent` union. Map item types:
       `item.completed` (when file write) → `IterCompleteEvent`
       (Step-1) or feeds the refine watcher (Step-2);
       `command_execution` → `ToolCallStart/End`; text deltas →
       `TextEvent`; `thread.started` carries the `session_id`.
       Publish all events to `EventBus` under key `(workdir,
       "iter")` (Step 1) or `(workdir, "refine:" + set_id)` (Step 2).
- [x] B.3 Capture `session_id` from the first event that carries
       one; persist via `write_sessions(workdir, ...)` under
       `iter` (for start) or `refine.<set_id>` (for refine).
- [x] B.4 Register Popen in `_lifecycle._LIVE` on spawn,
       deregister in reader-thread `finally`. Implement
       `CodexRunner.cancel(workdir, *, slot)` by delegating to
       `_lifecycle.terminate_slot(workdir, slot)`.
- [x] B.5 Implement `CodexRunner.refine(workdir, *, baseline_iters,
       message, adjustments)`:
       a. Compute `set_id = compute_set_id(baseline_iters)`.
       b. Acquire per-`(workdir, set_id)` lock; if held, raise
          `RefineInFlight` (server translates to HTTP 409).
       c. Translate `adjustments` to prose via
          `adjustments_to_prose.to_prose`; combine with `message`.
       d. Look up `sessions.json` → `refine.<set_id>`:
          - if absent: build system prompt via
            `refine_prompt.build_system_prompt(...)`, invoke
            `codex exec --json --system-prompt <…> "<user_msg>"`.
          - if present: invoke `codex exec resume <sid> --json
            "<user_msg>"` (no system prompt resent).
       e. Reader thread publishes events to EventBus under
          `(workdir, "refine:" + set_id)`; on `RefineCompleteEvent`
          (synthesized when `refine_NNN.json` appears on disk and
          is renamed-in), return the dict to the caller.
       f. Append both user + assistant turns to `chat.jsonl` via
          `chat_log.append_turn` with `set_id` + `baseline_iters`.
       g. If new session-id captured, update `sessions.json`.
- [x] B.6 "Session not found" recovery: if `codex exec resume` exits
       with stderr matching a known "session not found" signature
       (TBD by reading actual codex error output during impl),
       log warning, fall through to the turn-1 branch.
- [x] B.7 `CodexRunner.status(workdir)`: read status sidecar or
       in-memory state; same shape as MockRunner.
- [x] B.8 Unit tests under `tests/figcopy_runner/test_codex.py`:
       mock `subprocess.Popen` to inject canned JSONL streams;
       verify session-id capture, event translation to
       `SessionEvent` union, file writes, registry entries, cancel
       path, lock semantics.
- [x] B.9 `figcopy_serve.py`: add `--backend codex` plumbing (still
       not the default).
- [x] **Checkpoint B**: `pytest` green for codex tests; manual
       smoke with `--backend codex`:
       - submit a real run with a real reference image; watch iters
         appear (Step 1 working);
       - multi-select iters 1+3 and refine 3+ turns with NL
         messages (Step 2 multi-baseline + multi-turn working);
       - try the stepper to verify `adjustments` path;
       - check `sessions.json` shows `refine.<set_id>` matching
         `compute_set_id([1,3])`.

## Stage C — ClaudeRunner real subprocess wiring

Goal: `--backend claude` works end-to-end. Symmetric to Stage B.

- [ ] C.1 Implement `ClaudeRunner.start(workdir, prompt,
       max_iters)`: spawn `claude -p --output-format stream-json
       --verbose --include-partial-messages …` with cwd containing
       `.claude/skills/figure-style-copier/`.
- [ ] C.2 Stdout reader: translate claude's stream-json schema
       (different event names from codex's) to our `SessionEvent`
       union. Verify against current `claude` CLI version's actual
       output during implementation.
- [ ] C.3 Same session-id capture + `_lifecycle` registration as B.
- [ ] C.4 Implement `ClaudeRunner.refine(...)`: mirror B.5 but use
       `claude -p --resume <sid> --output-format stream-json ...`
       for follow-up turns. `--system-prompt` for turn 1.
- [ ] C.5 Same session-not-found recovery as B.6.
- [ ] C.6 Unit tests in `tests/figcopy_runner/test_claude.py`.
- [ ] C.7 `figcopy_serve.py`: add `--backend claude` plumbing.
- [ ] **Checkpoint C**: smoke `--backend claude` end-to-end with
       the same multi-baseline multi-turn check as B.

## Stage D — Server endpoints + SSE

Goal: chat history survives refresh; agent progress streams to the
browser; user can cancel.

- [x] D.1 Implement `GET /api/runs/<name>/chat/<set_id>` in
       `figcopy_serve.py`: read `{workdir}/chat.jsonl`,
       `chat_log.read_turns(workdir, set_id=set_id)`, return
       JSON array. 404 if workdir missing; `[]` if file or
       set_id has no entries.
- [x] D.2 Implement `GET /api/runs/<name>/chats`: scan
       `chat.jsonl`, group by `set_id`, return `[{set_id,
       baseline_iters, turn_count, last_ts}, ...]`.
- [x] D.3 Implement SSE handler infrastructure in
       `figcopy_serve.py`:
       - new helper `_write_sse_event(handler, seq, type, data)`
         that writes `id:\nevent:\ndata:\n\n` framing.
       - new helper `_serve_sse_stream(handler, session_key)`
         that:
         a. Parses `Last-Event-ID` header (default 0).
         b. Sets SSE headers + flushes.
         c. Replays `EventBus.replay(session_key, since=...)`.
         d. Subscribes a new queue; loops `queue.get(timeout=15)`
            (15 s keepalive: write `: ping\n\n` on timeout, retry).
         e. Writes each event; on `BrokenPipeError` /
            `ConnectionResetError`, unsubscribe + return.
- [x] D.4 Implement `GET /api/runs/<name>/chat/<set_id>/stream`:
       calls `_serve_sse_stream` with key `(workdir,
       "refine:" + set_id)`.
- [x] D.5 Implement `GET /api/runs/<name>/iter/stream`: calls
       `_serve_sse_stream` with key `(workdir, "iter")`.
- [x] D.6 Implement `POST /api/runs/<name>/cancel?slot=...`:
       parse `slot` (must be `iter` or `refine`); if `refine`,
       require `set_id=...`; call `runner.cancel(workdir, slot=...)`;
       return HTTP 204.
- [x] D.7 In `trajectory.js`:
       a. Add multi-select checkboxes to the iter strip + a
          floating action bar "Refine these N as a set →".
       b. Compute `set_id` client-side using the same algorithm
          (SHA-1[:8] of sorted-csv); navigate to
          `/r/<name>/refine?set=1,3,5`.
       c. New `initStep2` flow:
          i. Parse `set` from URL → `baseline_iters`.
          ii. Compute `set_id`.
          iii. `fetch(GET /api/runs/<name>/chat/<set_id>)` to
               hydrate prior turns.
          iv. Open `new EventSource("/api/runs/<name>/chat/<set_id>/stream")`.
          v. Wire event listeners: `text` → stream into pending
             assistant bubble; `tool_call_start/end` → render a
             tool activity row; `refine_complete` → finalize
             assistant bubble + add image + delta to controls panel;
             `turn_end` → close spinner.
       d. `postRefine` now sends `{run, baseline_iters,
          message, adjustments}`; receives full response (still
          synchronous for backward compat) but UI primarily
          reads from SSE.
       e. Add a Cancel button next to the spinner; POSTs
          `/api/runs/<name>/cancel?slot=refine&set_id=<sid>`.
       f. **Right panel — current working figure** (per
          design.md §D15 / spec "Phase-2 chat view shows the
          current working figure live"):
          - Initial render: if any `refine_NNN.png` exists for
            this set_id, show the highest-N one; else show the
            first baseline iter's `img_iter<X>.png` as
            placeholder.
          - On SSE `refine_complete` event: swap right-panel
            `<img src=...>` to the new `refine_NNN.png`.
       g. **Click-to-show-previous on the right-panel image**
          (per spec "Phase-2 right-panel click reveals the
          previous version"):
          - On click (or press-and-hold): swap to
            `refine_<N-1>.png` if it exists, else to
            `img_iter<first>.png` (sorted(baseline_iters)[0]).
          - Show an overlay label "previous (turn N-1)" or
            "baseline (iter X)".
          - On click again / release: revert to current
            `refine_<N>.png`.
       h. Phase-1 trajectory mode keeps the existing "click iter
          to reveal reference" gesture. Only Phase-2 chat mode
          uses the new click-to-show-previous behavior.
- [ ] D.8 In `workspace.js` (landing page):
       a. Replace the 3-s `/api/runs.json` poll with one
          EventSource per visible run: `/api/runs/<name>/iter/stream`.
          (Or: keep one poll for the run list itself, but switch
          per-run status to SSE. Prefer the simpler version: poll
          for list, SSE for active runs.)
       b. Iter-strip thumbnails update on `iter_complete` events
          without polling.
- [ ] D.9 In trajectory page HTML / `style.css`: surface
       agent-activity (tool calls) as a collapsible side panel
       on the trajectory view, populated from SSE
       `tool_call_start/end` events for Step 1.
- [x] D.10 Frontend handle for SSE `history_truncated` event:
       re-fetch full `GET /chat/<set_id>` to rehydrate.
- [ ] **Checkpoint D**: with `--backend codex`:
       a. Run Step 1, watch iters stream in via SSE (no polling
          flicker).
       b. Multi-select 3 iters, refine 3 turns, reload the tab,
          verify all 3 turns rehydrate AND SSE reconnects.
       c. Start a 4th refine, click Cancel mid-stream, verify
          spinner clears and a `turn_end:cancelled` event is
          received.
       d. Open the run from a second browser (different
          localStorage) and verify chat history loads from
          server.
       e. Verify right-panel working figure updates on each
          `refine_complete` event without page reload.
       f. Click the right-panel image: verify it shows
          `refine_<N-1>.png` with an overlay label; click again,
          verify it reverts.
       g. Trigger an agent internal-retry scenario (e.g. type
          something that makes the agent fail then fix itself):
          verify multiple `tool_call_start/end` pairs in the SSE
          stream and a single final `refine_complete` event.

## Stage E — Multi-chat schema groundwork (UI deferred)

Goal: server schema fully supports many chats per run; UI surfaces
one at a time but the foundation is laid.

- [ ] E.1 Verify `chat.jsonl` + `sessions.json` correctly hold
       multiple `set_id` entries per run with a manual test:
       refine two different baseline sets back-to-back; check
       both `GET /chats` returns 2 entries and `GET /chat/<sid>`
       responses are correctly filtered.
- [x] E.2 In `trajectory.js`, store the "current `set_id`" in
       the URL (`?set=1,3,5`) so navigating away and back lands
       on the same chat. Multiple-chat-list UI is **deferred to
       Phase 4** — Phase 3 surfaces one chat at a time but the
       URL is the routing primitive.
- [ ] E.3 Optional polish: add a "Start a new chat with these
       same baselines" affordance that opens a new tab with a
       nonce in `baseline_iters` (e.g. `[1,3,5,1]` — dedup
       would collapse; need a different mechanism, perhaps a
       `?nonce=` query param that changes the set_id hash).
       **Defer to Phase 4**; current set_id semantics mean
       re-selecting the same set always resumes.

## Stage F — Default flip + docs + polish

Goal: phase 3 ships as the new default. `--backend mock` is
opt-in.

- [x] F.1 Change `--backend` default from `mock` to `codex` in
       `figcopy_serve.py`. Add stderr deprecation warning when
       legacy `--mock` is passed.
- [x] F.2 Pre-flight check at server start: `shutil.which("codex")`
       (or `"claude"` for `--backend claude`); on failure, print
       an actionable error pointing at install docs; exit
       non-zero. **Skip the check for `--backend mock`.**
- [ ] F.3 Remove the `template_iter` backward-compat shim
       (A.11) — clients should now send `baseline_iters`.
- [ ] F.4 Update `scripts/README_figcopy_serve.md`:
       - new default backend,
       - the three `--backend` options,
       - new endpoints (chat / chats / chat-stream / iter-stream /
         cancel),
       - multi-baseline UX,
       - external prerequisites (`codex` and `claude` CLI),
       - explicit zero-runtime-deps callout.
- [ ] F.5 Update inline docstrings in `codex.py` and `claude.py` to
       remove "Phase 3 stub" language (replace with behavior
       summary + reference to design.md).
- [ ] F.6 Update interface.py docstring to reflect the new Runner
       Protocol shape (refine signature, multi-baseline, multi-turn
       contract).
- [ ] F.7 End-to-end integration test (`tests/integration/`) that
       boots `figcopy_serve` with `--backend mock`, submits a run,
       multi-selects iters via POST, posts 3 refines with prose,
       1 with adjustments, asserts: `chat.jsonl` shape,
       `sessions.json` shape, `GET /chat/<set_id>` response,
       SSE event sequence (via a stdlib SSE client helper).
- [ ] F.8 Delete the pattern-match table in `mock.py:309-337` (or
       move to a `tests/fixtures/` for use by tests only).
- [ ] F.9 Verify `_run_state` and trajectory CSS handle the
       `state: "cancelled"` value (phase 2 added running /
       shipped / failed; check `cancelled` renders correctly).
- [ ] F.10 Run `openspec validate phase3-real-runners-multi-turn-refine`
       and fix any spec-format issues.
- [ ] **Checkpoint F**: fresh clone simulation — `git clean -fdx`
       on a copy, `uv sync`, ensure `codex` on PATH, run
       `python scripts/figcopy_serve.py --workspace /tmp/ws` with
       no flags, drag a real reference + data, watch Step 1
       complete, multi-select baselines, refine 3+ turns, reload
       the tab. **Everything works without reading source code.
       Zero `pip install` beyond `uv sync` of stdlib-only project.**

## Stage G — Archive

- [ ] G.1 Once merged + verified, run
       `openspec archive phase3-real-runners-multi-turn-refine`
       to fold deltas into `openspec/specs/`.
