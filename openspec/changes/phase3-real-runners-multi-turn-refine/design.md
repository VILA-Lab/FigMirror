# Phase 3 design — real runner backend + multi-baseline multi-turn refine

> Reads alongside `proposal.md`. Implementation-level details are in
> `tasks.md`; this doc records the **architectural decisions** + the
> alternatives we evaluated so tasks don't re-litigate them.

## Context

### Current state (verified against the worktree, 2026-05-11)

- The webui (`figcopy_serve.py` + `figcopy_static/{workspace,trajectory}.js`)
  runs entirely against `MockRunner`. The only "fake" endpoint is
  **`/api/refine`** — it returns canned `{rcparams_delta, review}`
  produced by a hardcoded pattern-match table (`mock.py:309–337`).
  Every other endpoint reads disk truthfully.
- `CodexRunner` and `ClaudeRunner` in `scripts/figcopy_runner/` are
  phase-3 stubs — `start()` raises `NotImplementedError`, `status()`
  falls back to the on-disk `status.json` sidecar, `cancel()` no-ops.
- The `Runner` Protocol in `interface.py` declares `start / status /
  cancel` but **not `refine`** — yet `figcopy_serve.py:1428` calls
  `runner.refine(...)`. Mock happens to implement it.
- Step-2 chat lives in browser `localStorage` only — no server
  persistence, no cross-device, no rehydration.
- `pyproject.toml` ships `dependencies = []`. The server is single-file
  stdlib (`http.server` + `socketserver.ThreadingMixIn`). This is
  documented in `figcopy_serve.py:36` as a project principle, not an
  accident: *"Zero-dependency: stdlib only (no jinja, no flask).
  Single file."*

### Stakeholder model

Single-user, local-only. The product hypothesis is: a researcher
clones the repo, runs `uv sync`, runs one server command, drags a
paper figure + their data into the browser, and a few minutes later
sees a near-identical version of the figure rendered with their data.
That's the demo. Anything that makes the install step harder is a
direct attack on this hypothesis.

### Constraint that dominates everything below

**Zero new runtime dependencies.** Every "should we use X" answer in
this doc bottoms out at: does X violate `dependencies = []`? If yes,
the bar is "X buys us something we can't get from stdlib in ≤100 LOC."
Nothing crossed that bar.

## Goals / Non-Goals

**Goals:**

1. Replace the mock refine pattern-match with real agent calls.
2. Make Step-2 chat **multi-turn** — second turn knows what "smaller
   still" means.
3. Make Step-2 chat **multi-baseline** — user multi-selects a set of
   iters as the prompt context (Diffusion-style reference-image
   workflow); the agent treats them as a group.
4. Persist chat history server-side, indexed by a content-hash
   `set_id`. Re-selecting the same set resumes the same chat.
5. Stream agent progress to the browser in real time (token-level
   text + tool-call events + iter completions), for both Step 1 and
   Step 2.
6. Symmetric support for `codex` and `claude` backends.
7. Codify `refine` in the Runner Protocol.
8. **Keep `dependencies = []`.** No FastAPI, no Starlette, no SDK,
   no ACP, no SQLite.

**Non-Goals:**

- **WebSocket transport.** SSE handles our scale; WS's full-duplex
  is unused (POST endpoints handle the rare uplink: user message,
  cancel, file upload). See D2.
- **Cross-host session storage.** Local disk = source of truth.
- **Authentication.** Local-only.
- **Frontend chat-list UI** showing N chats per run as tabs. Server
  schema supports many chats; UI surfaces one at a time in Phase 3.
- **Interactive permission prompts.** Not needed for refine.
- **Changing the baseline set mid-conversation.** If the user wants
  a different set, they start a new chat (new `set_id`).
- **`.codex/skills/` and `.claude/skills/` modifications.** Skills
  already do their Step-1 job.

## Decisions

### D1. Stay on stdlib `http.server` — don't adopt FastAPI / Starlette / aiohttp

**Decision.** The server remains a single-file `http.server.BaseHTTPRequestHandler`
+ `socketserver.ThreadingMixIn` setup. SSE is implemented directly by
writing `text/event-stream` chunks to `self.wfile`.

**Rationale.**

- `pyproject.toml` is `dependencies = []`. **Adding a framework
  breaks the install story** (`uv sync` becomes "fetch 10–50
  packages including a Rust binary in pydantic-core"). The project's
  founding principle is to be a single-command-to-run tool; that's
  also the user-acquisition strategy ("zero install friction").
- **5–6 concurrent sessions is well within stdlib's capability.**
  `ThreadingMixIn` gives us one thread per request; an SSE stream
  is a long-lived request taking one thread. We are not at the
  scale where Python's GIL or thread overhead matters.
- **SSE in stdlib is ~30 LOC.** The complexity stdlib lacks
  (routing decorators, Pydantic validation, OpenAPI generation, etc.)
  is complexity we don't need at our size.
- **The existing server is already stdlib-only and working.**
  Adopting a framework now would be a from-scratch rewrite of a
  living codebase to gain nothing functional. Symmetric: any
  capability we'd lose isn't one we use.

**Alternatives considered.**

| Option | New deps | SSE story | Verdict |
|---|---|---|---|
| **stdlib (chosen)** | 0 | 30 LOC of `self.wfile.write` | **Pick** |
| `sse-starlette` (just for SSE) | starlette + anyio + sniffio + typing-extensions + sse-starlette ≈ 5 | One-line `EventSourceResponse` | Costs 5 deps to save 30 LOC |
| FastAPI (full framework) | ~12 incl. Rust binary | One-liner | Crushes the install story for benefits we don't use |
| aiohttp | ~6 incl. C extensions | Manual | Async we don't need, deps we don't want |
| Bottle | 1 (single file WSGI) | WSGI-SSE is unergonomic | Marginal win, real cost |

**Implication.** Our SSE implementation will hand-write the
`text/event-stream` framing and a simple event-bus. Code added is
≤200 LOC. We accept that responsibility for the dependency-zero
posture.

**Future migration.** If we ever do need async / WS / shared
state — `pyproject.toml` is the only file that changes the install
story. Pivoting in Phase 5+ to FastAPI is a refactor, not an
architectural change; the `Runner` Protocol and event union (D5)
are framework-agnostic.

### D2. SSE for server→browser streaming — don't use WebSocket

**Decision.** Use Server-Sent Events for all server→browser streaming
(both Step-1 iter progress and Step-2 chat). Browser→server traffic
(user messages, cancel, file upload) stays on plain POST endpoints.

**Rationale.**

- **Uplink is event-y, not continuous.** A user types one message
  per turn, clicks Cancel rarely, uploads a file once at run start.
  Each is a fine `POST`. There's no continuous client→server stream
  to justify full-duplex.
- **SSE has built-in reconnection with `Last-Event-ID`.** Browser's
  `EventSource` automatically reconnects on disconnect and sends
  the last seen event id; we replay from our ring buffer. With WS
  we'd write this ourselves.
- **SSE is plain HTTP** — no Upgrade-header proxy gotchas, plays
  nicely with stdlib `http.server`. WS is a different protocol after
  handshake; stdlib doesn't ship a WS server.
- **5–6 concurrent SSE connections is nothing.** Each takes one
  thread; HTTP/2 (which `http.server` doesn't speak, but it doesn't
  matter for our localhost case) would remove even the per-origin
  connection cap. We have no scale problem.

**Alternatives considered.**

- **WebSocket** (Omnara / Happy use this). Full-duplex unused at our
  scale; would force a third-party WS server (stdlib doesn't have
  one). Net negative for us.
- **Long polling.** Phase 2 effectively does this for `_state.json`;
  works but flickers and is bandwidth-heavy. SSE is strictly better.
- **HTTP/2 server push.** Not supported by `http.server`; would
  require a different server.

**Future migration.** A `HarnessAdapter`-style abstraction at the
event-bus boundary (each backend yields normalized events) means a
WS migration later is "change the wire framing in one place," not
"rewrite the backend."

### D3. Subprocess + stream-json — don't use SDK or ACP

**Decision.** Both `CodexRunner` and `ClaudeRunner` shell out to the
`claude` / `codex` CLI binaries via `subprocess.Popen` with
`--output-format stream-json` (claude) or `--json` (codex). We do
**not** add `claude-agent-sdk` or any ACP adapter.

**Rationale.**

- **Symmetry.** Codex has no production-stable Python SDK in
  2026-05 (TS-stable, Python-experimental); using `claude-agent-sdk`
  for one runner and subprocess for the other would create
  asymmetric lifecycle code. The phase-2 `claude.py:26-30` docstring
  already cites this reason for keeping the two runners parallel.
- **No new deps** (D1).
- **Reference patterns confirm this is the canonical choice.** Both
  Omnara (closed-source binary) and Happy (open-source) — the two
  production products closest to our shape — use subprocess +
  stream-json. Tech-stack survey (Phase 3 pre-design research)
  reached the same conclusion.
- **ACP isn't ready in 2026-05.** Neither Claude nor Codex ships
  native ACP; both rely on community adapters. Codex's ACP adapter
  lacks image input (breaking parity). The omi project, our nearest
  use-case neighbor that tried ACP, is documented as migrating away
  from it for production reasons.
- **The CLIs natively give us multi-turn**: `--resume <sid>` for
  claude, `exec resume <sid>` for codex. Both persist their own
  JSONL transcripts on disk; we own only the session-id ↔
  `(run, set_id)` mapping.

**Alternatives considered.** `claude-agent-sdk` (Python), ACP via
community adapters, direct Anthropic / OpenAI APIs without going
through Claude Code / Codex (loses skills, loses tool-use, defeats
the "harness reuse" goal). All evaluated; all rejected for the
reasons above.

**Implication.** We hand-write the JSONL parser for both CLIs (~50
LOC × 2). Both CLIs document their JSONL event shapes (Claude:
`code.claude.com/docs/en/headless`; Codex: `developers.openai.com/codex/noninteractive`).

### D4. Step 1 reuses skills; Step 2 uses an inline system prompt

**Decision.** `start()` invokes the agent CLI with the existing
skill on PATH (cwd contains `.codex/skills/figure-style-copier/` or
`.claude/skills/figure-style-copier/`; the CLI auto-discovers).
`refine()` builds the agent's system prompt **inside the runner
module** at call time and passes it via `--system-prompt` (claude)
or the equivalent first-message system frame (codex). No skill is
involved in Step 2.

**Rationale.**

- **Step 1 is a multi-step loop** (drawer → reviewer → drawer …)
  with internal tool calls — this is exactly what a skill is for.
- **Step 2 is one-shot per turn** — "look at these baselines, hear
  this message, output one png + one delta + one review." A skill
  would be overkill, and the multi-step loop in the skill body
  would be wrong for this interaction.
- **The user clarified the intent**: "agent 不能跑 loop" — refine
  is a single-shot interaction, not an agentic loop.
- **Phase-2 stub docstrings (`codex.py:5-18`, `claude.py:6-19`)
  already implied this**, though for partially-wrong reasons (they
  hinted codex lacks a skill system; that's actually false —
  codex's `.codex/skills/` is documented and supported, just
  feature-flagged). The *outcome* (inline prompt for Step 2) is
  still correct; the *reason* is "Step 2 isn't a loop", not "codex
  has no skills."

**Implication.** When we want to change Step-2 behavior we edit
Python (`scripts/figcopy_runner/refine_prompt.py`) — fast iteration,
diffable, testable. When we want to change Step-1 behavior we edit
the skill markdown.

### D5. Normalized event union across codex and claude

**Decision.** Define a single `SessionEvent` discriminated union
that both runners emit. Each runner translates its CLI's stream-json
into this shared schema at the subprocess-stdout boundary.

**Schema (Python `@dataclass`, no Pydantic dep — `__match_args__` +
`typing.Union` is enough):**

```python
@dataclass
class TextEvent:
    seq: int
    type: Literal["text"]
    data: dict       # {"text": "...", "is_partial": True}

@dataclass
class ToolCallStartEvent:
    seq: int
    type: Literal["tool_call_start"]
    data: dict       # {"call_id": "...", "name": "Read", "args": {...}}

@dataclass
class ToolCallEndEvent:
    seq: int
    type: Literal["tool_call_end"]
    data: dict       # {"call_id": "...", "ok": True, "result": "..."}

@dataclass
class TurnStartEvent:
    seq: int
    type: Literal["turn_start"]
    data: dict       # {"set_id": "...", "turn_index": 3}

@dataclass
class TurnEndEvent:
    seq: int
    type: Literal["turn_end"]
    data: dict       # {"status": "completed" | "failed" | "cancelled"}

@dataclass
class IterCompleteEvent:
    seq: int
    type: Literal["iter_complete"]
    data: dict       # {"iter": 3, "img_url": "...", "pdf_url": "..."}

@dataclass
class RefineCompleteEvent:
    seq: int
    type: Literal["refine_complete"]
    data: dict       # {"set_id": "...", "image_url": "...",
                     #  "rcparams_delta": {...}, "review": "..."}

SessionEvent = Union[TextEvent, ToolCallStartEvent, ToolCallEndEvent,
                     TurnStartEvent, TurnEndEvent,
                     IterCompleteEvent, RefineCompleteEvent]
```

**Rationale.**

- **This is the parity layer.** Codex and Claude emit different raw
  JSON; normalizing at the runner boundary means the frontend writes
  one event handler per `event:` type, not two per backend.
- **Happy's `packages/happy-wire/src/sessionProtocol.ts` is the
  reference pattern** (Tech-stack survey identified this as the
  single most-valuable file to crib).
- **Per-event sequence number is the foundation of SSE reconnect.**
  `Last-Event-ID` ← `seq` ← ring-buffer index.

**Implication.** Frontend code paths multiply per event-type, not
per backend. Backend code paths multiply per backend's CLI quirks,
not per event-type. Each axis varies independently.

### D6. Session-id ownership — runner persists `sessions.json`

**Decision.** Each workdir has a `sessions.json`:

```json
{
  "iter": "<sid for the Step-1 loop, if it has been run>",
  "refine": {
    "<set_id>": "<sid for that baseline set's chat>",
    ...
  }
}
```

The runner writes this file (atomic `.tmp` + rename) on first turn
of each session; reads it on every subsequent turn to decide
`--resume <sid>` vs. start-new.

**Rationale.** Canonical pattern from the tech-stack survey: "let
the CLI own the transcript; you only own the session-id mapping."
Re-sending transcripts ourselves would fight the CLI's
auto-compaction and break tool-use fidelity.

### D7. `set_id = sha1(sorted_iters)[:8]`, content hash

**Decision.** `set_id` is the first 8 hex chars of SHA-1 over the
sorted comma-joined baseline iter list:

```python
def compute_set_id(baseline_iters: list[int]) -> str:
    canonical = ",".join(str(i) for i in sorted(set(baseline_iters)))
    return hashlib.sha1(canonical.encode()).hexdigest()[:8]
```

**Rationale.**

- **Deterministic round-trip:** re-selecting `{1, 3, 5}` always maps
  to the same `set_id` → the same chat resumes. Matches the
  least-surprise model for "I selected these images again, I should
  see my previous conversation."
- **Independent of selection order**: `{5, 3, 1}` and `{1, 3, 5}`
  hash identically.
- **8 hex chars = 2³² space** — collision risk negligible for our
  use (a single workdir's set count is at most low-double-digits).
- **No UUID server-side state needed.** The set_id is *derivable
  from the input*, no DB lookup.

**Alternative considered.** UUID per "new chat" click. Rejected —
the round-trip property is the whole point.

**Trade-off.** User can't "wipe a chat and start fresh on the same
set." They'd have to pick a slightly different set, or we'd add a
"clear chat" button that deletes the session-id from
`sessions.json` (Phase 4 polish).

### D8. Step 2 has no `rcparams` agent-input — convert to prose server-side

**Decision.** When the user clicks a direct-control stepper
(`font.size = 15`), the frontend POSTs `{adjustments: {"font.size":
15}}` to `/api/refine`. The server then translates this into a
natural-language fragment (`"Adjust: font.size = 15"`) and feeds it
to the agent as the turn's user message. The agent itself never
sees a structured `rcparams` field.

**Rationale.**

- **Single agent input modality** (natural language) keeps the
  agent's session transcript consistent — direct-control turns and
  typed-message turns interleave cleanly.
- **The user already said** Step 2 prompt is server-side prompt
  engineering: "弹 UI ... 只是把 prompt engineering 的东西做得更顺
  滑、舒适一些." Direct controls are UX sugar; the agent doesn't
  need to know they exist.
- **Easier to evolve the controls UI** without touching the
  agent-side prompt format.

**Implication.** `adjustments_to_prose.py` is a small server-side
helper. Easy to test (input → output is pure).

### D9. Cancellation — SIGTERM → 3 s → SIGKILL

**Decision.** A module-level subprocess registry keyed by
`(workdir_path_str, slot)` where `slot ∈ {"iter", "refine:" +
set_id}`. `runner.cancel(workdir, slot=...)` finds the matching
`Popen` and calls `terminate()`; after 3 s grace, `kill()`.
`status.json` (for iter) or `chat.jsonl` (for refine) records a
final `cancelled` event.

**Rationale.** Canonical. Three seconds is enough for the CLI to
flush, short enough to feel responsive.

### D10. Default backend = `codex`, both first-class

**Decision.** `figcopy_serve.py --backend {mock,codex,claude}`
defaults to `codex`. Codex and Claude are equally supported. Mock
is opt-in for offline dev.

**Rationale.** No technical reason to prefer one — both are real,
both go through identical Runner Protocol. Default codex chosen on
the user's say-so ("默认用 codex 的使用体验更好"). Swapping is a
one-flag change.

**Pre-flight check.** On server start, `shutil.which("codex")` (or
"claude" for `--backend claude`) is verified; if missing, server
exits with an actionable error pointing at install docs. This
matters for the "uv sync && one command" demo path.

## Clarifications (things we got wrong in earlier drafts)

These were errors in the first version of this doc; recording them
so we don't repeat the misconception:

1. **Codex DOES have a skill system.** Earlier draft implied
   otherwise. `.codex/skills/<name>/SKILL.md` is the documented
   convention; OpenAI's own repo uses it. The skill system is
   feature-flagged but stable. We use it for Step 1.

2. **`.codex/AGENTS.md` may not be auto-discovered.** Codex looks
   for `AGENTS.md` along the git root → cwd path, not inside
   `.codex/`. Our repo's `.codex/AGENTS.md` is likely never loaded
   automatically. Out of scope for Phase 3; documented for future
   cleanup.

3. **Performance argument for "agent vs server runs matplotlib"
   was confused.** Earlier draft suggested server-side matplotlib
   could be "warm/long-running." It can't — both paths spawn a
   Python interpreter per render. The real trade-off is
   architectural (consistency with Step-1 skill flow), not
   performance. Decision: agent runs matplotlib (D11).

### D11. Agent runs matplotlib in `refine()`, retries internally — no server fallback

**Decision.** The refine subprocess (the `claude` / `codex` CLI)
runs matplotlib itself to produce `refine_NNN.png` via the agent's
own Python tool calls. The agent **retries internally** within a
single refine turn — writes code → runs it → inspects output →
fixes errors → reruns → until the png renders successfully. Only
then does the agent emit `refine_complete` and the server unblocks
the `/api/refine` response. There is **no server-side PIL fallback**;
there is no "error" response from a partial refine — if the agent
truly cannot render, the turn fails as a whole (`turn_end:failed`).

**Rationale.**

- **Consistent with Step 1.** Step 1's skill loop has the agent
  drive matplotlib; making Step 2 different (server-side render)
  would create two parallel render pipelines.
- **The agent sees its own output.** When the agent's first attempt
  produces a malformed png (text clipped, axes mislabeled, code
  errored), it can read the file back, diagnose, and try again — in
  the same turn. The user sees one chat reply; the server sees one
  `refine_complete`. Internal iteration is invisible to the API
  contract.
- **Server is rendering-agnostic.** No `matplotlib`, no `PIL` import
  on the server-side path. Server only watches the workdir for the
  atomic appearance of `refine_NNN.png` + `refine_NNN.json`.
- **The frontend still gets fine-grained feedback** — the agent's
  internal retry attempts surface as `tool_call_start/end` SSE
  events ("running Python: refine_007.py", "running Python:
  refine_007.py" again with edits, ...), so the user watches the
  agent fix its own mistakes in real time. This is a UX win, not a
  cost.

**Implication.** The refine prompt (D4) must explicitly instruct
the agent: *"Run matplotlib yourself. Inspect the rendered image.
If it has errors (clipping, missing labels, exception traceback),
fix and rerun. Only declare done when the png is correct."* This
text lives in `scripts/figcopy_runner/refine_prompt.py`.

### D12. Turn-1 system prompt includes compressed historical context

**Decision.** Each new agent session's turn-1 system prompt (built
by `refine_prompt.build_system_prompt`) SHALL include — in addition
to the baseline iters' artifacts and the accumulated rcparams
snapshot — a **compressed view of the most recent refine history
on this run**, formed by:

- The **last 3** completed refines from any `(workdir, set_id)` on
  this run, OR
- The **first + last** completed refines if more than 3 exist (so
  the agent sees both the original baseline state and the most
  recent state, even on long-running runs).

For each included refine, the prompt embeds **only**:

- The `refine_NNN.py` source (the matplotlib code the agent wrote).
- The `refine_NNN.json` contents (`rcparams_delta`, `review`).
- The `baseline_iters` that produced it.

It does **NOT** embed:

- The agent's own chat transcript (free-form prose, noisy, not
  structurally useful).
- The user's chat messages (the user's *intent* is implicit in the
  resulting `rcparams_delta` + the new turn-1 message).
- The PNG bytes (the agent can fetch the file by path if it wants;
  bloating the system prompt with images is wasteful).

**Rationale.**

- **Cross-session continuity.** When the user has been working in
  chat A (`set_id=abc12345`) for an hour and starts a new chat B
  (`set_id=def67890`, different baseline set), the new session's
  agent has no idea what was already accomplished. Without the
  history, the user's "字大了" (font got too big!) is meaningless
  to a fresh agent. With the history, the agent sees "ah, font.size
  was bumped 11 → 13 → 15 in the previous chat; user is complaining
  about the cumulative change."
- **Code + review = structured outcomes.** The
  `refine_NNN.py` + `refine_NNN.json` pair captures *what was
  actually changed and why*, structurally. The free-form chat
  transcript is the noisy version of the same information.
- **Compression: 3 turns, or first+last.** Avoids ballooning the
  system prompt on a long run; preserves both the "origin" and
  "now" snapshots which are the most useful poles.
- **The user articulated this directly**: include "前几轮 delta"
  but not the agent's own transcript.

**Implication.** `build_system_prompt` reads
`{workdir}/refine_*.{py,json}` files (sorted by NNN), picks 3 most
recent OR first+last per the rule above, embeds them in the prompt
under a clearly labeled section ("# Recent refine history on this
run (for context)"). The function MUST be deterministic — same
inputs → same prompt — so test fixtures are easy to write.

### D13. Refine completion is atomic on `refine_NNN.json` rename

**Decision.** The server determines a refine turn is "done" only
when `{workdir}/refine_NNN.json` appears via the `.tmp` + rename
sequence. While the agent retries internally, the server sees:

- `tool_call_start/end` events for each Python run (visible to the
  user via SSE — they watch the agent's internal retry loop).
- Possibly partial `.refine_NNN.png.tmp` files (ignored by glob).
- Possibly intermediate `refine_NNN_attempt_M.py` files (the agent's
  scratch; the prompt asks the agent to use clear filenames so the
  final artifact is unambiguous).

Only when both `refine_NNN.png` AND `refine_NNN.json` exist
(rename-completed) does the runner emit `RefineCompleteEvent` and
unblock the `/api/refine` response.

**Rationale.**

- **API contract is binary**: refine returns success or fails. The
  agent's internal struggle isn't a partial state at the API layer.
- **SSE shows the struggle** — front-end users get full visibility
  without polluting the response payload.
- **`.tmp` + rename is the existing project pattern** — no new
  atomicity discipline introduced.

**Trade-off.** If the agent never produces a valid png (e.g.
gives up, or hits its turn budget), the refine call eventually
times out (default 5 min). The server emits `turn_end:failed`,
the SSE consumer renders an error chat entry, and the user can
retry or rephrase. No silent partial states.

### D14. No hard cap on baseline count

**Decision.** `baseline_iters` accepts 1 or more iters with no
server-enforced upper limit. UI documents "2–3 baselines is
typical" as guidance, not as a constraint.

**Rationale.**

- **Arbitrary caps feel arbitrary.** "Why 5? Why not 7?" is a
  question with no good answer; better to not have one.
- **Real cost is the system-prompt size** (each baseline embeds a
  PNG path + Python source). At 10 baselines the prompt is still
  well within agent context budgets in 2026.
- **Users self-regulate.** The use case the user described — "I
  like image 1's layout, image 3's colors" — naturally hovers at
  2–3. Selecting 12 baselines is a self-correcting bad idea.

**Implication.** No validation rejecting `len(baseline_iters) > N`.
README mentions the typical range in a note, not a rule.

### D15. Phase 2 chat shows the working figure live; click reveals previous version

**Decision.** The Phase 2 chat page displays the **current working
figure** (the latest `refine_NNN.png`) prominently in the right
panel of the chat view, updated **live via SSE** as new refines
complete (`RefineCompleteEvent → image swap`). The reference image
that Phase 1's trajectory page shows on iter click is **replaced in
Phase 2** by a "show previous" interaction: clicking the current
working figure reveals the **previous** `refine_NNN.png` (the
version before the most recent refine), so the user can see what
changed turn-over-turn.

**Rationale.**

- **The user articulated this clearly**: in Phase 2 the "click to
  see reference" gesture should become "click to see previous
  iteration." The mental model is "I just refined; show me what
  it looked like before."
- **Live update of the right panel** is what makes the chat feel
  like a real interactive session — the user types, agent works
  (visible in the activity stream + tool calls), final image
  appears on the right within seconds.
- **Previous-revealing is a tiny UI hack** (swap `src` on click /
  hover) — no new server endpoint. Both `refine_NNN.png` and
  `refine_<N-1>.png` are already in the workdir.

**Implication.**
- Trajectory.js Phase-2 mode: right panel renders the latest
  `refine_NNN.png` (URL via the existing `/static/<run>/...`
  serving); subscribes to SSE `refine_complete` events and swaps
  `src` when one arrives.
- Click handler on the right panel: temporarily swaps to
  `refine_<N-1>.png` (or the latest baseline_iter's `img_iterX.png`
  if no previous refine exists). Click again to revert.
- `GET /api/runs/<name>/chats` SHALL NOT embed thumbnails (deferred
  / scratched; the live right panel handles the visibility need).

## Removed Open Questions

The earlier draft of this doc kept an "Open Questions" section to
flag undecided implementation details. Per the user, **Phase 3
ships with all design decisions resolved**; the questions are
folded into the decisions above as follows:

- Turn-1 system prompt content → D12.
- Agent vs. server-side rendering for refine → D11 (kept) with
  agent-internal retry semantics → D13.
- Baseline count cap → D14 (no cap).
- Chat-list thumbnails → D15 (no; right-panel live update instead).
- Agent returning delta vs. snapshot → handled inside D11's
  agent-internal retry contract (the agent inspects its output;
  whatever format it emits, the runner consumes via the agreed
  `refine_NNN.json` schema — if the agent emits a snapshot, the
  runner diffs against the prior accumulated state and stores the
  delta).

## Risks / Trade-offs

- **[Risk] First refine turn cold-start cost** (CLI spawn + skill
  / context load) can be 5–15 s slower than a resumed turn. →
  Mitigation: SSE shows partial text within ~1 s of subprocess
  spawn; UI feels responsive even when the final output is slow.

- **[Risk] `codex exec --json` or `claude --output-format
  stream-json` event schemas may shift between CLI versions.** →
  Mitigation: pin minimum CLI versions in README; runners' JSONL
  parsers log + skip unknown event types rather than crash.

- **[Risk] Stdlib SSE has no batteries.** → Mitigation: it's ~30
  LOC; we own it. If maintenance pain ever exceeds the value of
  zero-dep, revisit.

- **[Risk] Multi-baseline prompt context + history inflates
  first-turn prompt size.** Many baselines × (PNG path + Python
  source) plus D12's last-3-or-first+last refine history can grow
  the prompt. → Mitigation: typical use is 2–3 baselines (D14
  documents this as guidance); even at 10 baselines × 3 history
  entries the agent's 2026 context budget absorbs it. No hard cap
  — user self-regulates.

- **[Risk] Browser leaks SSE connections on tab close.** → Mitigation:
  `BrokenPipeError` handler in the SSE write loop deregisters the
  subscriber; subscribers also get a 15-second timeout on the queue
  read to detect stale connections.

- **[Risk] Concurrent refines on the same `(run, set_id)`** (user
  double-clicks Send). → Mitigation: per-`(workdir, set_id)` lock
  in the runner; second request gets 409 Conflict.

- **[Risk] Cancelled subprocess leaves partial files.** → Mitigation:
  `.tmp` + rename means partial files are `.tmp`-suffixed; server
  glob ignores them.

- **[Trade-off] No `claude-agent-sdk` means we hand-write JSONL
  parsing.** Small cost (~50 LOC × 2). Buys symmetry with Codex +
  zero new deps.

- **[Trade-off] Hand-written stdlib SSE bus.** ~80 LOC. We own all
  of it. Diagnoseable; no library mysteries.

## Migration Plan

Phase 3 is strictly additive — nothing the phase-2 UI did breaks.
The deprecated `--mock` flag still works (with a warning).

1. **Land protocol + event union + event bus first.** Protocol
   change in `interface.py`, shared `SessionEvent` types, in-memory
   `EventBus` class, `chat.jsonl` append helper. Zero user-visible
   change; mock backend exercises all the new code paths.
2. **Implement `CodexRunner`** (subprocess, JSONL parsing → event
   union, session-id persistence, refine flow). Behind
   `--backend codex` opt-in; default stays mock.
3. **Implement `ClaudeRunner`** (symmetric).
4. **Add SSE endpoints** (`/iter/stream`, `/chat/<set_id>/stream`)
   + frontend `EventSource` integration.
5. **Add multi-baseline UI** (multi-select checkbox on iter strip,
   `set_id` URL routing, chat list endpoint).
6. **Flip default to `--backend codex`** in a separate small commit.

**Rollback.** Any step can be reverted independently;
`--backend mock` restores phase-2 behavior even if real runners
break.

<!-- Open Questions section intentionally removed; all phase-3
     decisions are resolved above. See D11–D15 + "Removed Open
     Questions" subsection. -->

