# figcopy-webui-workpanel (delta)

> Phase-3 modifications to the phase-2 spec.

## MODIFIED Requirements

### Requirement: Step 2 chat refinement returns structured rcParams deltas

Step 2 SHALL switch from a single-template chat to a **multi-baseline
multi-turn chat** keyed by content-hash `set_id`. After the user
multi-selects 1+ iters as baselines, the page SHALL show a chat view
in which each user message produces a new image plus a structured
rcParams delta; the UI SHALL surface the accumulated rcParams as
direct controls. Chat history SHALL be **persisted server-side** in
`{workdir}/chat.jsonl` (with localStorage as a write-through cache)
and SHALL be **multi-turn** within a `(run, set_id)` scope (the
agent remembers prior turns).

#### Scenario: First refinement turn with multi-baseline

- **GIVEN** the user has multi-selected iter 2 and iter 4 as
  baselines, and the resulting `set_id = "abc12345"`
- **WHEN** the user types "字大一点" and submits
- **THEN** the server SHALL POST it as `{run, baseline_iters: [2,4],
  message: "字大一点"}` and the response SHALL be a real agent's
  output (not pattern-matched).
- **AND** the chat log SHALL show the user message and the AI reply
  with the new image inline.
- **AND** `{workdir}/chat.jsonl` SHALL contain one new user line +
  one new assistant line, each tagged with `set_id: "abc12345"` and
  `baseline_iters: [2, 4]`.
- **AND** `{workdir}/sessions.json` SHALL contain a new
  `refine.abc12345` session-id entry.

#### Scenario: Direct-control adjustment is server-side prompt-engineered

- **GIVEN** a controls panel with `font.size: 13` rendered after a
  prior NL turn
- **WHEN** the user clicks the stepper to set `font.size: 15`
- **THEN** the page SHALL POST to `/api/refine` with `{run,
  baseline_iters, adjustments: {"font.size": 15}}` (no NL message).
- **AND** the server SHALL translate `adjustments` into a NL message
  (e.g. `"Adjust: font.size = 15"`) and feed THAT to the agent.
- **AND** the chat log SHALL show the original action ("set
  font.size = 15") plus the agent's response and the new image.

#### Scenario: Multi-turn context continuity

- **GIVEN** the user has already sent "make legend smaller" on
  `baseline_iters=[2,4]` and the agent returned
  `{legend.fontsize: 9}`
- **WHEN** the user sends "smaller still" in the same chat
- **THEN** the response's `rcparams_delta` SHALL reflect awareness
  of the prior turn (e.g. `{legend.fontsize: 8}`).
- **AND** all turns SHALL share a single `refine.abc12345`
  session-id in `sessions.json`.

#### Scenario: Re-selecting the same set resumes the chat

- **GIVEN** the user previously had 3 turns on
  `baseline_iters=[2,4]` then navigated away
- **WHEN** the user multi-selects iter 2 and iter 4 again and opens
  the Step-2 chat
- **THEN** the same `set_id = "abc12345"` SHALL be computed.
- **AND** the chat log SHALL hydrate the 3 prior turns from
  `GET /api/runs/<name>/chat/abc12345`.
- **AND** sending a 4th message SHALL continue the same agent
  session (verifiable: `sessions.json` is unchanged; CLI invoked
  with `--resume <sid>`).

#### Scenario: Different baseline sets get independent chats

- **GIVEN** the user has refined `baseline_iters=[2,4]` 3 times
- **WHEN** the user multi-selects `baseline_iters=[2,4,7]` (added
  iter 7) and starts a new chat
- **THEN** the new `set_id` SHALL be different from `abc12345`.
- **AND** the chat history SHALL be empty (no prior turns).
- **AND** a new agent session SHALL be started.

#### Scenario: Page refresh rehydrates from server

- **GIVEN** the user has had 4 refine turns on a baseline set
- **WHEN** the browser tab is reloaded
- **THEN** the page SHALL fetch `GET /api/runs/<name>/chat/<set_id>`
  and rehydrate 8 chat entries (4 user + 4 assistant).
- **AND** the rehydrated chat SHALL match what was on screen before
  reload (modulo asset URLs).

### Requirement: MockRunner is the default backend in Phase 2

The webui SHALL ship with three runner implementations
(`MockRunner`, `CodexRunner`, `ClaudeRunner`) selected at server
start via `--backend {mock,codex,claude}`. **[Phase 3 delta:
header retained; semantics flipped.]** The default backend SHALL
be `codex` (not `mock`); `MockRunner` remains available for offline
development and CI via `--backend mock`. Codex and Claude SHALL be
equally first-class.

#### Scenario: Submit a run with default backend

- **GIVEN** the user filled the New Run form
- **AND** the server was started with no `--backend` flag
- **WHEN** the user clicks `Run`
- **THEN** `CodexRunner.start` SHALL be invoked.

#### Scenario: Submit a run with --backend mock

- **GIVEN** the server was started with `--backend mock`
- **WHEN** the user clicks `Run`
- **THEN** `MockRunner.start` SHALL be invoked.

#### Scenario: Submit a run with --backend claude

- **GIVEN** the server was started with `--backend claude`
- **WHEN** the user clicks `Run`
- **THEN** `ClaudeRunner.start` SHALL be invoked.

### Requirement: Trajectory page shows Step 1 horizontal iter strip with progressive disclosure

The per-run trajectory page SHALL show all completed iters as a
horizontal-scroll strip; detailed review information SHALL be
hidden until the user clicks an iter. **The strip SHALL support
multi-select via per-iter checkboxes** (or shift-click range).
A floating action bar SHALL appear once 1+ iter is selected,
offering "Refine these N as a set" which routes to Step 2 with
the corresponding `set_id`.

#### Scenario: User opens a run with 8 iters

- **GIVEN** a run with 8 completed iters
- **WHEN** the user opens `/r/<name>`
- **THEN** the page SHALL show all 8 iters in a horizontal strip,
  each rendering its image, a checkbox, and a click target for
  expansion.

#### Scenario: User multi-selects three iters

- **GIVEN** iters 1, 3, 5 are visible in the strip
- **WHEN** the user checks iters 1, 3, 5
- **THEN** a floating action bar SHALL show
  `"Refine these 3 as a set →"`.
- **AND** clicking the bar SHALL navigate to
  `/r/<name>/refine?set=1,3,5` (or equivalent URL); the page SHALL
  enter Step 2 chat mode with `set_id = sha1("1,3,5")[:8] =
  "abc12345"`.

#### Scenario: Re-entering with the same set

- **GIVEN** the user previously refined `[1,3,5]`
- **WHEN** the user navigates to the trajectory page, re-selects
  the same three iters, and clicks "Refine these 3 as a set"
- **THEN** the Step 2 chat SHALL load with the prior turns
  hydrated.

## ADDED Requirements

### Requirement: `/api/refine` accepts multi-baseline payloads and dispatches to the runner

`POST /api/refine` SHALL accept JSON `{run: str, baseline_iters:
list[int], message?: str, adjustments?: dict}`. The server SHALL
compute `set_id` from `baseline_iters`, call `runner.refine(workdir,
baseline_iters=..., message=..., adjustments=...)`, and return the
runner's response dict (with `image_url` rewritten to a
`/static/<run>/...` URL).

#### Scenario: Real backend NL message

- **GIVEN** the server uses the codex backend
- **WHEN** the client POSTs `{run: "r1", baseline_iters: [2,4],
  message: "字大一点"}`
- **THEN** the server SHALL call `CodexRunner().refine(workdir,
  baseline_iters=[2,4], message="字大一点")`.
- **AND** the response SHALL include `{image_url, rcparams_delta,
  review, set_id, seq}`.

#### Scenario: Both message and adjustments

- **WHEN** the client POSTs `{run, baseline_iters, message: "blue
  please", adjustments: {"font.size": 15}}`
- **THEN** the server SHALL pass both to the runner, which combines
  them into a single user message for the agent (e.g. `"Adjust:
  font.size = 15. blue please"`).

#### Scenario: Concurrent refine on same set returns 409

- **GIVEN** a refine on `set_id=abc12345` is in flight
- **WHEN** a second POST to `/api/refine` arrives for the same set
- **THEN** the server SHALL respond HTTP 409.

### Requirement: `GET /api/runs/<name>/chat/<set_id>` returns chat history filtered by set_id

The server SHALL expose `GET /api/runs/<name>/chat/<set_id>` that
reads `{workdir}/chat.jsonl`, filters to entries with the matching
`set_id`, and returns a JSON array.

#### Scenario: History exists

- **GIVEN** `set_id=abc12345` has 3 refine turns recorded in
  `chat.jsonl`
- **WHEN** the client requests `GET /api/runs/r1/chat/abc12345`
- **THEN** the response SHALL be a JSON array of 6 entries (3 user +
  3 assistant), each carrying `role`, `content`, `ts`, `set_id`,
  `baseline_iters`, and (assistant only) `image_url`,
  `rcparams_delta`, `review`, `seq`.

#### Scenario: No history yet

- **WHEN** the client requests a set_id with no entries
- **THEN** the response SHALL be HTTP 200 with body `[]`.

#### Scenario: Unknown run

- **WHEN** the client requests `GET /api/runs/<missing>/chat/<sid>`
- **THEN** the response SHALL be HTTP 404.

### Requirement: `GET /api/runs/<name>/chats` lists all chats for a run

The server SHALL expose `GET /api/runs/<name>/chats` returning a
JSON array of all distinct `set_id` values for which `chat.jsonl`
has entries, with metadata: `{set_id, baseline_iters, turn_count,
last_ts}`.

#### Scenario: Run with two chats

- **GIVEN** the user has refined `[2,4]` 3 times and `[1,3,5]`
  twice on run `r1`
- **WHEN** the client requests `GET /api/runs/r1/chats`
- **THEN** the response SHALL be a 2-element array, each entry with
  the correct `baseline_iters`, `turn_count` (6 and 4 entries
  respectively in chat.jsonl, so `turn_count: 3` and `turn_count:
  2`), and `last_ts`.

### Requirement: SSE endpoint streams normalized SessionEvents for a chat

The server SHALL expose `GET /api/runs/<name>/chat/<set_id>/stream`
as a Server-Sent Events endpoint. Each event SHALL be framed as:

```
id: <seq>
event: <type>
data: <json>
\n
```

The endpoint SHALL honor the `Last-Event-ID` request header by
replaying buffered events with `seq > Last-Event-ID` before
streaming live events.

#### Scenario: Initial connection streams live events

- **GIVEN** the user opens the Step-2 chat page; the
  `EventSource(/api/runs/r1/chat/abc12345/stream)` is created
- **WHEN** the user submits a refine; the agent begins emitting
  events
- **THEN** the SSE connection SHALL receive `text` events
  (streaming the assistant's reply text), `tool_call_start` /
  `tool_call_end` (if the agent uses tools),
  `turn_start` / `turn_end`, and a final `refine_complete` event
  with the same payload the POST response carries.

#### Scenario: Reconnect after disconnect

- **GIVEN** the browser disconnected after receiving `seq=42`;
  events `seq=43..50` were emitted while disconnected
- **WHEN** the `EventSource` automatically reconnects (with
  `Last-Event-ID: 42`)
- **THEN** the server SHALL replay events `seq=43..50` from the
  ring buffer.
- **AND** subsequent live events SHALL continue from `seq=51`.

#### Scenario: Buffer truncation surfaces to client

- **GIVEN** the ring buffer cap is 1000 and the session has emitted
  2500 events
- **WHEN** a client reconnects with `Last-Event-ID: 100`
- **THEN** the server SHALL emit a control event
  (`event: history_truncated`, with a `since` field indicating the
  oldest available seq) before resuming normal events.
- **AND** the client SHALL respond by re-fetching
  `GET /api/runs/<name>/chat/<set_id>` to rehydrate full history.

### Requirement: SSE endpoint streams iter-loop events

The server SHALL expose `GET /api/runs/<name>/iter/stream` as an
SSE endpoint emitting `text`, `tool_call_start`, `tool_call_end`,
`iter_complete`, and `turn_end` events from the Step-1 iter loop.

#### Scenario: Watching Step 1 live

- **GIVEN** Step 1 is mid-iter-3
- **WHEN** the client opens
  `EventSource(/api/runs/r1/iter/stream)`
- **THEN** the client SHALL receive live `text` / `tool_call_*`
  events as the agent works.
- **AND** when iter 3 finishes, an `iter_complete` event with
  `{iter: 3, img_url, pdf_url}` SHALL be emitted.

### Requirement: `POST /api/runs/<name>/cancel` cancels an iter loop or a refine

The server SHALL expose `POST /api/runs/<name>/cancel` accepting
query parameters `slot=iter` OR `slot=refine&set_id=<id>`.

#### Scenario: Cancel an iter loop

- **GIVEN** a run is in `state: running, current_iter: 3`
- **WHEN** the client POSTs `/api/runs/r1/cancel?slot=iter`
- **THEN** the response SHALL be HTTP 204.
- **AND** within 5 seconds, `status.json` SHALL be updated to
  `{"state": "cancelled"}`.

#### Scenario: Cancel a refine

- **GIVEN** a refine on `set_id=abc12345` is in flight
- **WHEN** the client POSTs
  `/api/runs/r1/cancel?slot=refine&set_id=abc12345`
- **THEN** the response SHALL be HTTP 204.
- **AND** the refine subprocess SHALL be terminated; the
  outstanding `/api/refine` POST SHALL receive HTTP 499.

### Requirement: Frontend hydrates chat from server then opens SSE

`trajectory.js` `initStep2` SHALL on entry:

1. Compute `set_id` from the multi-selected `baseline_iters`.
2. Fetch `GET /api/runs/<name>/chat/<set_id>` to hydrate any prior
   turns.
3. Open `new EventSource("/api/runs/<name>/chat/<set_id>/stream")`
   to receive live events.

The page SHALL store the highest seen `seq` in a JS variable for
`Last-Event-ID` resilience (browser's `EventSource` handles this
automatically, but the page SHALL also tolerate manual reconnects).

#### Scenario: Hydration succeeds, SSE opens

- **WHEN** `initStep2` runs for a set with 3 prior turns
- **THEN** the chat log SHALL render 6 hydrated entries.
- **AND** a live `EventSource` SHALL be open for `<set_id>/stream`.

#### Scenario: Stale localStorage cleared on server contradiction

- **GIVEN** localStorage has 5 cached turns for `<set_id>` but the
  server returns only 3
- **WHEN** hydration completes
- **THEN** localStorage SHALL be overwritten with the server's 3
  turns.

### Requirement: Trajectory page uses SSE for Step-1 progress

`trajectory.js` SHALL replace the existing 3-second poll of
`_state.json` with an `EventSource` on
`/api/runs/<name>/iter/stream` for live iter updates. The iter
strip SHALL append new iter cards as `iter_complete` events arrive.
Tool-call activity (if surfaced) SHALL appear in a collapsible
"agent activity" panel.

#### Scenario: Iter completion appears without polling

- **GIVEN** Step 1 is running, the iter strip currently shows iters
  0..2
- **WHEN** iter 3 completes (server emits `iter_complete`)
- **THEN** the iter strip SHALL add the iter-3 card within ~200 ms
  (latency of SSE delivery), without a periodic poll.

### Requirement: Phase-2 chat view shows the current working figure live in a right panel

The Phase-2 chat page SHALL render a right panel that displays the
**latest `refine_NNN.png`** for the active `(run, set_id)`. The
panel SHALL subscribe to the SSE stream and update its image source
to the new png as soon as each `refine_complete` event arrives —
no page reload required.

#### Scenario: Working image updates as refines complete

- **GIVEN** the user is in Phase-2 chat with prior turn N=5
  showing `refine_005.png` in the right panel
- **WHEN** the user sends a new message, the agent works, and a
  `refine_complete` event with N=6 arrives via SSE
- **THEN** the right panel SHALL swap its image source to
  `refine_006.png` within ~200 ms.
- **AND** no full-page reload SHALL occur.

#### Scenario: First refine on a brand-new set

- **GIVEN** the user just multi-selected baselines and entered a
  Phase-2 chat for the first time (`set_id` has no prior refines)
- **WHEN** the page first renders
- **THEN** the right panel SHALL show **the first baseline iter's
  png** (`img_iter<first>.png`) as a placeholder — there is no
  refine yet to show.
- **AND** on the first `refine_complete` event, the panel SHALL
  swap to `refine_001.png` of this set.

### Requirement: Phase-2 right-panel click reveals the previous version

The Phase-2 chat right-panel SHALL respond to a click (or
press-and-hold) on the working figure by temporarily swapping the
displayed image to the **previous** `refine_<N-1>.png` (or, if no
previous refine exists for this set, the relevant baseline iter's
`img_iterX.png`). Releasing the click / clicking again SHALL
restore the current `refine_<N>.png`.

This replaces the Phase-1 "click iter to reveal reference"
gesture for the Phase-2 context.

#### Scenario: Toggle to previous after several refines

- **GIVEN** the user has done 4 refine turns on a set; the right
  panel shows `refine_004.png`
- **WHEN** the user clicks the right-panel image
- **THEN** the panel SHALL display `refine_003.png`.
- **AND** a small overlay SHALL label it "previous (turn 3)".
- **WHEN** the user clicks again (or releases a press-and-hold)
- **THEN** the panel SHALL revert to `refine_004.png`.

#### Scenario: First refine of this set — fall back to baseline

- **GIVEN** the right panel shows `refine_001.png` (only one
  refine completed on this set)
- **WHEN** the user clicks the working figure
- **THEN** the panel SHALL display the **first baseline iter's
  image** (`img_iter<first>.png` where `first = sorted(baseline_iters)[0]`).
- **AND** the overlay SHALL label it "baseline (iter <first>)".

### Requirement: In-flight refine UI exposes a Cancel affordance

While a refine is pending, the chat UI SHALL show a "Cancel" button
next to the spinner. Clicking it SHALL POST
`/api/runs/<name>/cancel?slot=refine&set_id=<sid>`.

#### Scenario: User cancels mid-refine

- **GIVEN** the user sent a refine; the spinner is showing
- **WHEN** the user clicks Cancel
- **THEN** the POST SHALL be issued.
- **AND** the spinner SHALL be replaced with a "Cancelled" notice;
  no new chat entry SHALL be appended.
- **AND** the SSE connection SHALL receive a `turn_end` event with
  `status: "cancelled"` confirming the server-side termination.
