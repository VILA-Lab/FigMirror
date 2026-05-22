# figcopy-real-runner-backend

> Real subprocess-driven runner backends that drive the `codex` and
> `claude` CLIs for both Step 1 (iter loop via existing skill) and
> Step 2 (multi-baseline multi-turn chat refinement via inline system
> prompt). See proposal.md §Why + design.md §Decisions for rationale.

## ADDED Requirements

### Requirement: Zero new runtime dependencies

The runner backend SHALL be implemented using Python's standard library
only. `pyproject.toml` SHALL continue to declare `dependencies = []`.

#### Scenario: Cold install path

- **GIVEN** a fresh clone of the repo with no caches
- **WHEN** the user runs `uv sync`
- **THEN** no third-party Python packages SHALL be installed as
  runtime dependencies.
- **AND** the only external prerequisites SHALL be the `codex`
  and/or `claude` CLI binaries on `$PATH`.

### Requirement: Runner Protocol includes `refine` with multi-baseline signature

The `Runner` Protocol in `scripts/figcopy_runner/interface.py` SHALL
declare a `refine` method with this exact signature:

```python
def refine(
    self,
    workdir: Path,
    *,
    baseline_iters: list[int],
    message: str | None = None,
    adjustments: dict | None = None,
) -> dict: ...
```

The return value SHALL be a dict with keys `image_url: str`,
`rcparams_delta: dict`, `review: str`, `set_id: str`, `seq: int`.
The runner SHALL be **multi-turn-safe** — successive calls on the
same `(workdir, sorted(baseline_iters))` MUST share an agent
session.

#### Scenario: Protocol satisfied by all runners

- **WHEN** the codebase is type-checked
- **THEN** `MockRunner`, `CodexRunner`, and `ClaudeRunner` SHALL all
  satisfy the Protocol's `refine` signature.

#### Scenario: Caller does not manage session-id

- **GIVEN** the server's `/api/refine` handler holds no session-id
  state
- **WHEN** it calls `runner.refine(workdir, baseline_iters=[1,3,5],
  message="smaller")` twice in a row
- **THEN** the second call's response SHALL reflect awareness of the
  first call's context, **without** the server passing any
  session-id through the call.

### Requirement: `set_id` is a content hash of the sorted baseline list

The runner SHALL derive `set_id` deterministically from
`baseline_iters` as the first 8 hex chars of the SHA-1 of the
comma-joined sorted deduplicated iter list.

```python
canonical = ",".join(str(i) for i in sorted(set(baseline_iters)))
set_id = hashlib.sha1(canonical.encode()).hexdigest()[:8]
```

#### Scenario: Set order does not matter

- **GIVEN** two refine requests with `baseline_iters=[1,3,5]` and
  `baseline_iters=[5,3,1]` respectively (same workdir, same message)
- **WHEN** both are submitted
- **THEN** they SHALL be routed to the same agent session (same
  `set_id`).

#### Scenario: Different sets get different sessions

- **GIVEN** the user has refined `baseline_iters=[1,3,5]` 3 times,
  then submits a new request with `baseline_iters=[1,3,5,7]`
- **WHEN** the new request is processed
- **THEN** the runner SHALL start a fresh agent session (different
  `set_id`); the prior 3-turn context SHALL NOT carry over.

### Requirement: CodexRunner.start invokes the codex skill via subprocess

`CodexRunner.start(workdir, prompt, max_iters)` SHALL spawn the
`codex` CLI as a subprocess (`codex exec --json …` with appropriate
sandbox + skill discovery) with cwd set so that
`.codex/skills/figure-style-copier/` is discoverable. The call SHALL
return within 200 ms while the subprocess runs in a daemon thread.

#### Scenario: Start a fresh run

- **WHEN** the server calls `CodexRunner().start(workdir, prompt="…",
  max_iters=4)`
- **THEN** a `subprocess.Popen` of `codex exec --json …` SHALL be
  spawned with stdout / stderr captured.
- **AND** the agent's session-id SHALL be captured from the first
  JSONL event carrying one, and written atomically to
  `{workdir}/sessions.json` under the `iter` key.
- **AND** `{workdir}/status.json` SHALL transition `running` →
  `shipped` (zero exit) or `failed` (non-zero / exception) as the
  subprocess progresses.
- **AND** no file under `.codex/skills/figure-style-copier/` SHALL
  be created, modified, or deleted by the runner.

### Requirement: ClaudeRunner.start invokes the claude skill via subprocess

`ClaudeRunner.start(workdir, prompt, max_iters)` SHALL spawn the
`claude` CLI as a subprocess (`claude -p --output-format stream-json
--verbose --include-partial-messages …`) with cwd set so that
`.claude/skills/figure-style-copier/` is discoverable.

#### Scenario: Start a fresh run

- **WHEN** the server calls `ClaudeRunner().start(workdir, …)`
- **THEN** the JSONL event stream SHALL be parsed; session-id
  captured and written to `{workdir}/sessions.json`.
- **AND** status.json transitions SHALL match `CodexRunner`
  semantics.

### Requirement: Step-2 refine uses an inline system prompt, not a skill

Both `CodexRunner.refine` and `ClaudeRunner.refine` SHALL build the
agent's system prompt **inside the runner module** at call time.
They SHALL NOT invoke any skill for refine.

#### Scenario: First refine turn passes an inline system prompt

- **GIVEN** no existing session for `(workdir, set_id)`
- **WHEN** `runner.refine(workdir, baseline_iters=[2,4], message="字大一点")`
  is called
- **THEN** the subprocess SHALL be invoked with a system prompt
  whose text references:
  - each baseline iter's PNG path (`img_iter2.png`, `img_iter4.png`),
  - each baseline iter's Python source,
  - the current accumulated rcparams snapshot (empty dict on first
    turn),
  - the compressed history of recent refines on this run
    (see "Turn-1 system prompt includes recent refine history"
    requirement below),
  - an output contract instructing the agent to write
    `refine_NNN.png` + `refine_NNN.json` into the workdir AND
    explicitly authorizing the agent to retry internally within
    the turn until the png renders successfully (see "Agent retries
    rendering internally" requirement below).
- **AND** no file under `.codex/skills/` or `.claude/skills/` SHALL
  be referenced or invoked by the refine path.

### Requirement: Turn-1 system prompt includes recent refine history

The runner's `build_system_prompt` SHALL include a "Recent refine
history on this run" section embedding **either the last 3
completed refines OR the first + last refines** across all `set_id`
values on this run, whichever applies:

- If 0 prior refines exist: omit the history section.
- If 1–3 prior refines exist: include all of them, oldest first.
- If 4+ prior refines exist: include the first refine plus the most
  recent 2 (so the agent sees both origin and current state).

For each included refine, the prompt SHALL embed **only**:

- The contents of `refine_NNN.py` (the matplotlib source).
- The contents of `refine_NNN.json` (`rcparams_delta` + `review`).
- The `baseline_iters` that produced it.

The prompt SHALL NOT embed:

- The agent's chat transcript (free-form prose).
- The user's chat messages from prior turns.
- The PNG bytes (agent can read by path if needed).

#### Scenario: Fresh run, no history

- **GIVEN** a workdir with no `refine_*.json` files
- **WHEN** `build_system_prompt` is called for the first refine
- **THEN** the prompt SHALL NOT include a history section.

#### Scenario: 2 prior refines

- **GIVEN** the workdir contains `refine_001.{py,json}` and
  `refine_002.{py,json}`
- **WHEN** `build_system_prompt` is called for a turn-1 on a new
  `set_id`
- **THEN** the prompt SHALL contain a history section with both
  refines, oldest first.
- **AND** each entry SHALL include the `.py` source + the `.json`
  contents + the `baseline_iters` recorded in that refine.
- **AND** the section SHALL NOT include any text from `chat.jsonl`.

#### Scenario: 7 prior refines (first + last)

- **GIVEN** the workdir contains `refine_001..refine_007`
- **WHEN** `build_system_prompt` is called for a fresh session
- **THEN** the history section SHALL include exactly 3 refines:
  `refine_001`, `refine_006`, `refine_007` (first + last 2).

#### Scenario: Determinism

- **GIVEN** identical workdir state
- **WHEN** `build_system_prompt(workdir, baseline_iters=[2,4],
  accumulated_rcparams={})` is called twice
- **THEN** the two prompt strings SHALL be byte-identical.

### Requirement: Agent retries rendering internally; refine_complete only on success

The refine system prompt SHALL explicitly instruct the agent to
treat matplotlib rendering as a self-correcting loop within the
turn: write code, run it, inspect the output PNG (or the Python
traceback if rendering errored), fix problems, and only declare
the refine done when the PNG renders correctly.

The runner SHALL NOT emit `RefineCompleteEvent` until **both**
`refine_NNN.png` AND `refine_NNN.json` exist in the workdir via
the `.tmp` + rename atomic sequence. The runner SHALL NOT
implement any server-side PIL or matplotlib fallback path.

#### Scenario: Agent succeeds on first attempt

- **GIVEN** a refine turn is in progress
- **WHEN** the agent's first matplotlib invocation produces a valid
  PNG that is renamed into place
- **THEN** the runner SHALL emit `RefineCompleteEvent` once
  `refine_NNN.json` also lands.

#### Scenario: Agent retries internally on render failure

- **GIVEN** a refine turn is in progress
- **WHEN** the agent's first matplotlib invocation throws (e.g.
  `KeyError` on an rcparam), the agent reads the traceback, edits
  the code, reruns matplotlib, and the second attempt succeeds
- **THEN** the SSE stream SHALL show multiple `tool_call_start /
  tool_call_end` event pairs covering each Python execution.
- **AND** the runner SHALL emit `RefineCompleteEvent` only after
  the final successful png+json pair lands.
- **AND** the `/api/refine` response SHALL contain the final
  rcparams_delta + review (not any intermediate failed-attempt
  state).

#### Scenario: Agent gives up

- **GIVEN** a refine turn is in progress
- **WHEN** the agent reaches its turn budget without producing a
  valid png+json pair
- **THEN** the runner SHALL emit `TurnEndEvent` with
  `status: "failed"` (not `RefineCompleteEvent`).
- **AND** the `/api/refine` response SHALL be HTTP 5xx with
  `{"error": "refine_failed", "set_id": "..."}`.
- **AND** no `refine_NNN.{png,json}` SHALL be left in the workdir
  from this attempt (the `.tmp` + rename discipline guarantees
  this: incomplete files stay `.tmp`-suffixed and can be cleaned
  up).

### Requirement: No server-enforced upper bound on baseline_iters

The runner SHALL accept any `baseline_iters` list with at least 1
entry. There SHALL be no maximum-length validation.

#### Scenario: User selects 1 baseline

- **WHEN** the client POSTs `baseline_iters=[3]`
- **THEN** the runner SHALL accept and process it.

#### Scenario: User selects 12 baselines

- **WHEN** the client POSTs `baseline_iters=[0,1,2,3,4,5,6,7,8,9,
  10,11]`
- **THEN** the runner SHALL accept and process it (the system
  prompt will be larger but no error is raised on size).

#### Scenario: Empty baseline list

- **WHEN** the client POSTs `baseline_iters=[]`
- **THEN** the runner SHALL raise a `ValueError` (or the server
  SHALL return HTTP 400) — at least one baseline is required.

### Requirement: Multi-turn refine resumes the per-`(run, set_id)` session

The runner SHALL reuse the session-id captured on turn 1 for every
successive refine call on the same `(workdir, set_id)`, via
`--resume <sid>` (claude) or `exec resume <sid>` (codex). On
follow-up turns, only the new user message SHALL be in the prompt
body (no inline system prompt re-sent).

#### Scenario: Second turn refers to the first

- **GIVEN** the user has already refined `baseline_iters=[2,4]` once
  with `"make legend smaller"` and the agent returned
  `legend.fontsize: 9`
- **WHEN** the user sends a second message `"smaller still"` on the
  same set
- **THEN** the runner SHALL look up the prior session-id from
  `sessions.json` under `refine.<set_id>`.
- **AND** the subprocess SHALL be invoked with `--resume <sid>` and
  only `"smaller still"` as the prompt body.
- **AND** the returned `rcparams_delta` SHALL reflect awareness of
  `legend.fontsize: 9` (e.g. `{legend.fontsize: 8}`).

### Requirement: Adjustments are converted to natural-language prompts server-side

The runner SHALL translate any structured `adjustments` dict
received via `/api/refine` into a natural-language message fragment
(e.g. `"Adjust: font.size = 15"`) and feed it to the agent as the
turn's user message. The agent SHALL NOT see a structured
`adjustments` field.

#### Scenario: Stepper click sends a prose message to the agent

- **GIVEN** the user clicks the `font.size` stepper to set value 15
- **WHEN** the request reaches the runner with `adjustments:
  {"font.size": 15}` and no `message`
- **THEN** the runner SHALL synthesize the user message `"Adjust:
  font.size = 15"` (or equivalent prose) and pass that to the
  subprocess.
- **AND** the `chat.jsonl` user-line entry SHALL record both the
  original `adjustments` field and the synthesized prose for audit.

### Requirement: Session-id persistence is atomic and durable

`{workdir}/sessions.json` writes SHALL use the `.tmp` + rename
idiom. The file SHALL survive server restarts; runners SHALL
read it on every refine call to decide turn-1 vs. resume.

```json
{
  "iter": "<sid for the Step-1 loop>",
  "refine": {
    "<set_id_1>": "<sid_1>",
    "<set_id_2>": "<sid_2>",
    ...
  }
}
```

#### Scenario: Server restart mid-conversation

- **GIVEN** `set_id=abc12345` has had 3 successful refine turns; the
  server is restarted
- **WHEN** the user sends a 4th message on the same set
- **THEN** the runner SHALL read `sessions.json`, find
  `refine.abc12345 = "<sid>"`, and invoke the subprocess with
  `--resume <sid>`.
- **AND** the 4th turn's response SHALL reflect awareness of the
  prior 3 turns.

#### Scenario: CLI lost the session on its side

- **GIVEN** `sessions.json` records `refine.<set_id> = "<sid>"` but
  the CLI's local session-store has GC'd that session
- **WHEN** the runner invokes `--resume <sid>` and the subprocess
  exits with a "session not found" error
- **THEN** the runner SHALL log a warning, treat the next refine as
  turn 1 (including the inline system prompt), capture the new
  session-id, and update `sessions.json`.

### Requirement: Output files are written atomically

Both runners SHALL produce `refine_NNN.png`, `refine_NNN.json`, and
the appended `chat.jsonl` line only after a `.tmp` + rename
sequence.

#### Scenario: Server poll during agent write

- **GIVEN** the agent has written `.refine_007.png.tmp` but not yet
  renamed
- **WHEN** the server globs `refine_*.png` for a chat-list response
- **THEN** the partial file SHALL NOT be matched.

### Requirement: Normalized SessionEvent union emitted by both runners

Both runners SHALL parse the CLI's stream-json output and emit a
normalized `SessionEvent` discriminated union with at minimum these
variants:

- `TextEvent` — assistant text delta
- `ToolCallStartEvent` — agent invoked a tool
- `ToolCallEndEvent` — tool call completed
- `TurnStartEvent` — agent began a turn
- `TurnEndEvent` — agent finished a turn (`status: completed |
  failed | cancelled`)
- `IterCompleteEvent` — Step-1 completed one iter
- `RefineCompleteEvent` — Step-2 produced one refine output

Every event SHALL carry a monotonically-increasing `seq: int` scoped
to its session (`(workdir, "iter" | set_id)`).

#### Scenario: Frontend handles events without backend awareness

- **GIVEN** the frontend SSE consumer listens on
  `event: text`, `event: tool_call_start`, etc.
- **WHEN** the user switches from `--backend codex` to `--backend
  claude` (server restart with same workdir)
- **THEN** the same event-handler code SHALL render both backends'
  outputs without per-backend branching.

#### Scenario: Sequence numbers monotonically increase

- **GIVEN** an active session with `seq=42` last emitted
- **WHEN** a new event is published
- **THEN** the new event's `seq` SHALL be 43 (or higher in the case
  of multiple concurrent emit paths, but never lower or duplicate).

### Requirement: In-memory event bus with per-session ring buffer

The runner module SHALL provide an `EventBus` with:

- `publish(session_key, event)` — append event, increment per-session
  seq, notify all subscribers.
- `subscribe(session_key) -> queue.Queue` — return a new subscriber
  queue.
- `unsubscribe(session_key, queue)` — remove a subscriber.
- `replay(session_key, since_seq) -> Iterable[Event]` — yield
  buffered events with `seq > since_seq`.

The bus SHALL maintain a bounded ring buffer (default 1000 events)
per `session_key` for reconnect replay.

#### Scenario: Reconnect replays missed events

- **GIVEN** a session has emitted seq 1..50; a subscriber disconnects
  after seq 30; events seq 31..50 emit while disconnected
- **WHEN** the subscriber reconnects with `since_seq=30`
- **THEN** `replay(session_key, 30)` SHALL yield events seq 31..50.
- **AND** further `publish` events SHALL be delivered to the new
  subscriber queue in real time.

#### Scenario: Ring buffer cap

- **GIVEN** a session has emitted 2000 events with buffer cap 1000
- **WHEN** a subscriber reconnects with `since_seq=500`
- **THEN** the replay SHALL yield only events that are still in the
  buffer (~seq 1001..2000); the subscriber SHALL receive a control
  event indicating "history truncated" so the client can request a
  full rehydrate via `GET /api/runs/<name>/chat/<set_id>`.

### Requirement: Subprocess lifecycle uses a per-(workdir, slot) registry

The runner module SHALL maintain a module-level subprocess registry
keyed by `(workdir_path_str, slot)` where `slot ∈ {"iter",
"refine:<set_id>"}` that tracks every live `Popen`. Entries SHALL
be registered on spawn and deregistered on subprocess exit (in a
reader-thread `finally`).

#### Scenario: Two refines on different sets run concurrently

- **GIVEN** a refine on `set_id=abc12345` is mid-flight
- **WHEN** the user submits a refine on `set_id=def67890`
- **THEN** both subprocesses SHALL run concurrently; the registry
  SHALL contain entries `(<workdir>, "refine:abc12345")` and
  `(<workdir>, "refine:def67890")`.

### Requirement: Cancel sends SIGTERM, escalates to SIGKILL after 3 seconds

`runner.cancel(workdir, *, slot="iter" | "refine:<set_id>")` SHALL
terminate the subprocess registered under that slot. Idempotent. If
no subprocess is registered, the call SHALL return without raising.

#### Scenario: Cancel an iter loop

- **GIVEN** `CodexRunner.start(workdir)` is mid-iter-3
- **WHEN** the server calls `runner.cancel(workdir, slot="iter")`
- **THEN** the iter subprocess SHALL receive SIGTERM.
- **AND** if not exited within 3 seconds, SIGKILL SHALL be sent.
- **AND** `{workdir}/status.json` SHALL be updated to `{"state":
  "cancelled"}`.

#### Scenario: Cancel a refine

- **GIVEN** a refine on `set_id=abc12345` is in flight
- **WHEN** the server calls `runner.cancel(workdir,
  slot="refine:abc12345")`
- **THEN** the refine subprocess SHALL receive SIGTERM (escalating
  as above).
- **AND** a `TurnEndEvent` with `status: "cancelled"` SHALL be
  emitted to the event bus.
- **AND** the outstanding `/api/refine` request handler SHALL
  return HTTP 499 with body `{"error": "cancelled"}`.

#### Scenario: Cancel with no running subprocess

- **WHEN** `runner.cancel(workdir, slot="refine:nonexistent")` is
  called
- **THEN** the call SHALL return without raising.

### Requirement: `--backend` flag selects the runner; codex is default

`scripts/figcopy_serve.py` SHALL accept `--backend {mock,codex,claude}`
with **default `codex`**. The deprecated `--mock` flag SHALL remain
as an alias for `--backend mock` with a stderr deprecation warning.

#### Scenario: Default flips to codex

- **WHEN** the user runs `python scripts/figcopy_serve.py
  --workspace /tmp/ws` with no other flags
- **THEN** the server SHALL instantiate `CodexRunner()`.

#### Scenario: Pre-flight check fails actionably

- **WHEN** the server starts with `--backend codex` and `codex` is
  not on `$PATH`
- **THEN** the server SHALL print an actionable error
  (`"codex not found on PATH; install: https://..."`) and exit
  with non-zero status.

#### Scenario: Mock backend for offline dev

- **WHEN** the user runs `--backend mock`
- **THEN** `MockRunner()` SHALL be used; no CLI prerequisite check
  SHALL be performed.

#### Scenario: Legacy --mock emits deprecation warning

- **WHEN** the user runs `--mock` (no `--backend`)
- **THEN** the server SHALL behave as if `--backend mock` were
  passed AND SHALL print a deprecation notice to stderr.

### Requirement: Per-`(workdir, set_id)` refine lock prevents concurrent same-set turns

The runner SHALL hold a per-`(workdir, set_id)` lock for the
duration of each refine call. A second refine on the same
`(workdir, set_id)` while the first is in flight SHALL be rejected
with a 409 Conflict response (translated by the server from a
runner-level exception or sentinel return).

#### Scenario: User double-clicks Send

- **GIVEN** a refine on `set_id=abc12345` is already in flight
- **WHEN** a second POST to `/api/refine` arrives for the same set
- **THEN** the server SHALL respond HTTP 409 with body
  `{"error": "refine_in_flight", "set_id": "abc12345"}`.
