"""Runner interface — abstract backend contract for the figcopy loop.

Phase 2 shipped :class:`MockRunner` (synthesizes plausible iter files
into the workdir on a timer). Phase 3 fills in the real backends in
sibling modules:

- ``codex.py`` → :class:`CodexRunner` — drives the codex CLI
- ``claude.py`` → :class:`ClaudeRunner` — drives the claude CLI

The webui talks to whichever runner ``run_workspace`` instantiates;
all impls honor this shape so swapping is a one-line change.

Two on-disk sidecar contracts shared by every runner:

1. **Status sidecar** ``{workdir}/status.json`` (read by
   ``figcopy_serve._run_state``)::

       {
         "state": "running" | "shipped" | "failed" | "cancelled",
         "current_iter": 0..max_iters - 1,  # optional
         "reason": "fresh-context reviewer failed..."  # optional
       }

2. **Sessions sidecar** ``{workdir}/sessions.json`` (Phase 3; tracks
   agent session-ids so multi-turn refine can resume after server
   restart)::

       {
         "iter": "<agent session-id for Step-1 loop>" | null,
         "refine": {
            "<set_id>": "<agent session-id for that baseline set>",
            ...
         }
       }

   Where ``<set_id> = compute_set_id(baseline_iters)`` (SHA-1[:8] of
   the comma-joined sorted-deduped iter list — see ``compute_set_id``).

Atomicity: every file the runner writes (status.json, sessions.json,
img_iter<N>.png, audit_iter<N>.json, refine_NNN.png, refine_NNN.json,
chat.jsonl, …) MUST be written via the ``.tmp`` + rename idiom so the
server's poll-disk loop never observes a half-written file.

Phase 3 also adds a normalized ``SessionEvent`` union (see
``events.py``) that runners publish to an in-memory ``EventBus`` (see
``event_bus.py``). SSE endpoints in the server subscribe to that bus.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional, Protocol


# ─────────────────────────── atomic write ────────────────────────────


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically via ``.tmp`` + rename.

    Shared by every Phase-3 helper that touches the workdir (sessions,
    chat_log, status sidecar). POSIX rename is atomic — the server's
    poll-disk loop will see either the old contents or the new contents,
    never partial.

    Creates parents on demand. UTF-8.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_copy_file(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst`` atomically via ``.tmp`` + rename."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    tmp.replace(dst)


def ensure_reference_raw(workdir: Path) -> Optional[Path]:
    """Ensure ``inputs/reference_raw.png`` exists for Stage-0 preprocessing.

    New UI runs stage the upload to both ``reference_raw.png`` and
    ``reference_clean.png`` so the first paint can still show a
    reference before preprocessing completes. Older workdirs may only
    have ``reference_clean.png``; in that case preserve that file as
    the raw upload before the crop pass overwrites ``reference_clean``.
    """
    inputs = workdir / "inputs"
    raw = inputs / "reference_raw.png"
    clean = inputs / "reference_clean.png"
    if raw.exists():
        return raw
    if clean.exists():
        atomic_copy_file(clean, raw)
        return raw
    return None


# ─────────────────────────── status sidecar ──────────────────────────


# Run statuses where the persisted disk state is the source of truth
# and the workdir is treated as read-only browsable: a terminal run
# must NOT have its chat.jsonl, status.json, or any other sidecar
# mutated by a page-load / GET path. Originally introduced in
# ``figcopy_serve.py`` as ``_TERMINAL_RUN_STATUSES`` for the round-2
# backend-availability override; promoted here so other runner-side
# helpers (e.g. ``chat_log.recover_orphan_refines``) can honor the
# same invariant. The figcopy_serve module re-exports this as its
# private alias for backwards compatibility.
TERMINAL_RUN_STATUSES = frozenset({"shipped", "failed", "cancelled"})


def read_status_sidecar(workdir: Path) -> Optional[dict]:
    """Parse ``workdir/status.json`` if present + well-formed.

    Returns the parsed dict on success, ``None`` if the file is missing,
    unreadable, malformed JSON, or doesn't have the expected ``state``
    string field.
    """
    sj = workdir / "status.json"
    if not sj.exists():
        return None
    try:
        data = json.loads(sj.read_text())
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("state"), str):
        return None
    return data


_REVIEWER_PROTOCOL_FAILURE_MARKERS = (
    "codex exec reviewer unavailable",
    "codex exec reviewer attempted",
    "local reviewer json written",
    "nested cli could not access",
    "nested codex could not access",
    "managed identity",
    "local proxy",
    "failed to initialize in-process app-server client",
)


def reviewer_protocol_failure_reason(workdir: Path) -> Optional[str]:
    """Return a reason if Stage-1 violated reviewer isolation.

    The FigMirror contract requires a fresh-context Reviewer.
    A top-level agent may still exit 0 after writing local audit JSONs
    when that nested reviewer launch fails. Treat those runs as failed
    instead of letting optimistic self-audits mark them shipped.
    """
    failure_marker = workdir / "REVIEWER_FAILED"
    if failure_marker.is_file():
        try:
            detail = failure_marker.read_text(
                encoding="utf-8", errors="ignore"
            ).strip()
        except OSError:
            detail = ""
        detail = " ".join(detail.split())[:500]
        suffix = f": {detail}" if detail else ""
        return f"reviewer failed closed{suffix}"

    for p in sorted(workdir.glob("audit_iter*.stderr")):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lower = text.lower()
        if any(marker in lower for marker in _REVIEWER_PROTOCOL_FAILURE_MARKERS):
            return (
                f"fresh-context reviewer failed for {p.name}; "
                "audit JSON was not independently produced"
            )
    return None


def iteration_cap_failure_reason(
    iterations: list[int], max_iters: int
) -> Optional[str]:
    """Return a terminal failure when a runner produced an out-of-cap draft."""
    breached = [n for n in iterations if n >= max_iters]
    if not breached:
        return None
    return (
        f"iteration cap exceeded: found img_iter{min(breached)}.png with "
        f"max_iters={max_iters}"
    )


def terminal_review_decision(workdir: Path) -> Optional[dict]:
    """Return the final validated review action needed for runner fallback.

    The model process may exit after the deterministic helper commits `ship` or
    `stop_at_cap` but before it copies the selected image to `figure.png`. Runner
    fallback is allowed only for that narrow state; a provisional
    `review_same_draft`, an actionable `draw`, or an invalid retry is not a ship
    decision.
    """
    attempts_dir = workdir / "review_attempts"
    paths = sorted(attempts_dir.glob("attempt_*.json"))
    if not paths:
        return None
    for index, path in enumerate(paths):
        if path.name != f"attempt_{index:03d}.json":
            return None
    try:
        attempt = json.loads(paths[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(attempt, dict) or attempt.get("state") != "committed":
        return None
    if attempt.get("attempt") != len(paths) - 1:
        return None

    action = attempt.get("action")
    classification = attempt.get("classification")
    iter_idx = attempt.get("iter")
    min_reviews = attempt.get("min_reviews")
    max_iters = attempt.get("max_iters")
    valid_count = attempt.get("valid_review_count")
    ints = (iter_idx, min_reviews, max_iters, valid_count)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in ints):
        return None
    if min_reviews < 1 or max_iters < 1 or not 0 <= iter_idx < max_iters:
        return None

    if action == "ship":
        if classification != "all_clear" or valid_count < min_reviews:
            return None
    elif action == "stop_at_cap":
        if (
            classification != "actionable"
            or iter_idx != max_iters - 1
            or valid_count < 1
        ):
            return None
    else:
        return None

    draft = workdir / f"img_iter{iter_idx}.png"
    recorded_sha = attempt.get("draft_sha256")
    if (
        not draft.is_file()
        or not isinstance(recorded_sha, str)
        or re.fullmatch(r"[0-9a-f]{64}", recorded_sha) is None
    ):
        return None
    try:
        digest = hashlib.sha256(draft.read_bytes()).hexdigest()
    except OSError:
        return None
    if digest != recorded_sha:
        return None
    for canonical_review in (
        workdir / f"audit_iter{iter_idx}.json",
        workdir / f"review_feedback_{iter_idx}" / "review.json",
    ):
        try:
            if not canonical_review.is_file() or canonical_review.stat().st_size == 0:
                return None
        except OSError:
            return None
    return {"action": action, "iter": iter_idx}


# ─────────────────────────── sessions sidecar ────────────────────────


def read_sessions(workdir: Path) -> dict:
    """Parse ``workdir/sessions.json`` if present + well-formed.

    Returns ``{"iter": None, "refine": {}}`` (empty default) if the
    file is missing, unreadable, or malformed — runners should treat
    a missing sessions file the same as "no prior sessions exist."
    """
    sj = workdir / "sessions.json"
    default: dict = {"iter": None, "refine": {}}
    if not sj.exists():
        return default
    try:
        data = json.loads(sj.read_text())
    except Exception:
        return default
    if not isinstance(data, dict):
        return default
    # Normalize shape — accept partial files written by older runners.
    out: dict = {
        "iter": data.get("iter") if isinstance(data.get("iter"), (str, type(None))) else None,
        "refine": data["refine"] if isinstance(data.get("refine"), dict) else {},
    }
    return out


def write_sessions(workdir: Path, data: dict) -> None:
    """Persist the sessions sidecar atomically.

    ``data`` SHOULD be of shape ``{"iter": str|None, "refine":
    {set_id: sid, ...}}``; callers are responsible for preserving the
    shape when mutating (i.e., read → mutate → write, not blind
    overwrite).
    """
    atomic_write_text(
        workdir / "sessions.json",
        json.dumps(data, indent=2, sort_keys=True) + "\n",
    )


# ─────────────────────── refine in-flight markers ────────────────────


# Sidecar files of the form ``.refine_inflight_<set_id>`` are written
# by each runner at the TOP of ``_refine_locked`` (before any subprocess
# is spawned) and removed in a ``finally`` after ``append_turn``
# succeeds. They exist so ``chat_log.recover_orphan_refines`` can carve
# a hole in the terminal-status gate for a refine whose subprocess
# wrote ``refine_NNN.{png,json}`` but where the runner died (or the
# HTTP request was abandoned) before appending the assistant chat
# bubble.
#
# Why this is decoupled from ``sessions["refine"][set_id]`` (PR #25
# round-2 finding #1): ``sessions["refine"][set_id]`` is the agent's
# session/thread id, persisted by the runner AFTER the subprocess
# completes AND only when the agent emitted a session-id frame. That
# happens too late (and is too conditional) to serve as the in-flight
# signal — an early-crash variant where the subprocess writes the
# artifacts then SIGKILL's before the runner reaches the
# ``write_sessions`` call would never set the key. It also persists
# forever, so terminal-state runs from prior completed turns would
# permanently bypass the round-2 invariant for the same set_id.
#
# In-flight markers, by contrast, are written-at-entry and
# cleared-at-exit, matching the carve-out's docstring contract.
#
# Marker payload (PR #25 round-3 finding #2 + round-4 hardening): each
# marker file contains a single JSON object::
#
#     {
#       "pid": int,                  # writing process id
#       "start_ts": int,              # time.monotonic_ns() at write
#       "proc_starttime": int|None,   # /proc/<self>/stat field 22
#                                     # (clock ticks since boot, Linux
#                                     # only; None on hosts without
#                                     # /proc — caller falls back to TTL)
#       "set_id": str,                # echoed for cross-check
#       "refine_idx": int|None,       # the refine_NNN index this
#                                     # marker is bound to (round-4
#                                     # finding #1 — recovery must only
#                                     # clear the marker when the
#                                     # consumed orphan's refine_idx
#                                     # matches, so an older same-set
#                                     # orphan can't clear the current
#                                     # active runner's marker)
#     }
#
# Reader logic (``_pid_is_alive`` / ``_marker_alive_now``):
#
# - ``os.kill(pid, 0)`` filters out the canonical SIGKILL/host-OOM case.
# - ``proc_starttime`` cross-check defends against PID reuse: after a
#   runner crashes and its pid is reused by ANY long-lived process
#   (sshd reconnect, systemd unit restart, container respawn), the new
#   process's /proc/<pid>/stat field 22 differs from the marker's
#   stamped value → treat as dead. Linux-only (no /proc/<pid>/stat
#   on macOS); on other OSes the cross-check is skipped and we fall
#   through to the TTL check.
# - TTL fallback: if monotonic_ns elapsed since ``start_ts`` exceeds
#   ``MAX_REFINE_DURATION_NS``, treat the marker as stale regardless
#   of pid. This bounds the damage from PID-reuse on non-Linux hosts
#   AND from cross-boot stale markers (where the on-disk monotonic_ns
#   is meaningless after a reboot). It is stale-marker cleanup, not a
#   live refine turn timeout.
#
# So a crashed runner's marker stops widening the carve-out the moment
# the next reader runs (or, on non-Linux, after MAX_REFINE_DURATION_NS
# at worst). The dot prefix on the marker filename is load-bearing: it
# keeps the marker out of every sibling ``glob("refine_*")`` and
# ``glob("img_iter*")`` callsite. Future maintainers must NOT "tidy up"
# the dot prefix.

_INFLIGHT_PREFIX = ".refine_inflight_"
_REFINE_RESERVATION_PREFIX = ".refine_reserved_"

# Stale-marker TTL for orphan recovery. Live Step-2 refine turns have
# no wall-clock timeout by default; this only limits how long an orphan
# marker can widen terminal-run carve-outs after a crash.
MAX_REFINE_DURATION_NS = 30 * 60 * 1_000_000_000

# Defense-in-depth bound on ``reserve_refine_index``'s post-O_EXCL recheck
# loop (PR #26 round-2 advisory). In practice the loop terminates quickly
# because ``next_refine_index`` advances each iteration, but an unbounded
# ``while True`` is the only one in the allocator path; capping it at a
# generous value matches the surrounding "bounded retry, then raise"
# idiom and turns a hypothetical pathological spin into a loud failure.
# 1024 attempts is large enough to absorb any realistic concurrent
# allocator volume on a single workdir while still bounding the worst
# case to a fraction of a second.
MAX_RESERVE_ATTEMPTS = 1024

# ``set_id`` is the first 8 hex chars of SHA-1 (see ``compute_set_id``);
# every live caller passes that exact shape. Validating here defends
# the marker filename + path-join against a future caller that wires
# user-controlled input through this surface (path traversal via ``..``
# / ``/`` / null bytes).
_SET_ID_RE = re.compile(r"\A[0-9a-f]{8}\Z")


def _validated_set_id(set_id: str) -> str:
    if not isinstance(set_id, str) or not _SET_ID_RE.match(set_id):
        raise ValueError(
            f"set_id must be 8 lowercase-hex chars (got {set_id!r})"
        )
    return set_id


def _inflight_marker_path(workdir: Path, set_id: str) -> Path:
    return workdir / f"{_INFLIGHT_PREFIX}{_validated_set_id(set_id)}"


def _marker_payload(set_id: str, refine_idx: Optional[int] = None) -> str:
    """Serialize the marker payload for ``set_id``.

    ``pid`` is the current process id; ``start_ts`` is a monotonic
    nanosecond timestamp captured at write time; ``proc_starttime`` is
    field 22 of ``/proc/self/stat`` (clock ticks since boot, Linux
    only) used by the reader to detect PID-reuse — see
    ``_read_proc_starttime``. ``refine_idx`` is the ``refine_NNN`` index
    this marker is bound to (PR #25 round-4 finding #1) — readers use
    it to refuse clearing the marker when the consumed orphan's index
    doesn't match, so an older same-set orphan can't clear the current
    active runner's marker.
    """
    return json.dumps(
        {
            "pid": os.getpid(),
            "start_ts": time.monotonic_ns(),
            "proc_starttime": _read_proc_starttime(os.getpid()),
            "set_id": set_id,
            "refine_idx": refine_idx,
        },
        sort_keys=True,
    ) + "\n"


def _read_proc_starttime(pid: int) -> Optional[int]:
    """Return ``/proc/<pid>/stat`` field 22 (process start time) or None.

    Field 22 is the process start time in clock ticks since boot
    (``man 5 proc``). Linux-only; on macOS / Windows / hosts without
    procfs, returns ``None`` — callers fall back to TTL aliveness.

    The field is the 22nd whitespace-separated token AFTER the comm
    field (which is parenthesised). We slice off everything up to and
    including the trailing ``)`` of comm before splitting, so a comm
    that contains spaces or parens (e.g. ``(my proc)``) doesn't shift
    field indices.
    """
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    rparen = text.rfind(")")
    if rparen < 0:
        return None
    rest = text[rparen + 1:].split()
    # rest[0] corresponds to field 3 (state) → field 22 is rest[19].
    if len(rest) < 20:
        return None
    try:
        return int(rest[19])
    except ValueError:
        return None


def _read_marker_payload(path: Path) -> Optional[dict]:
    """Read + parse the JSON marker payload at ``path`` defensively.

    Returns:

    - ``None`` for legacy raw-set_id markers (payload doesn't start
      with ``{``) — caller treats as alive (fail-open) so an in-place
      upgrade across releases doesn't drop carve-outs.
    - ``None`` for unreadable files (caller decides — usually
      conservative: keep the marker).
    - the parsed dict for any well-formed JSON object (even if
      individual fields are bogus — callers validate per-field).

    The legacy/JSON distinction is the first non-whitespace byte: a
    ``compute_set_id`` output is 8 hex chars and never starts with
    ``{``, so this disambiguation is safe by construction.

    Use ``_classify_marker`` when the caller needs to distinguish
    "legacy raw" from "JSON-shaped but malformed" (round-4: malformed
    JSON markers are bugs and must be treated as DEAD).
    """
    kind, data = _classify_marker(path)
    if kind == "json":
        return data
    return None


def _classify_marker(path: Path) -> tuple[str, Optional[dict]]:
    """Return ``(kind, parsed)`` for the marker at ``path``.

    ``kind`` is one of:

    - ``"missing"`` — file unreadable (transient I/O error).
      ``parsed`` is None. Conservative caller treats as alive.
    - ``"legacy"`` — payload doesn't start with ``{`` (legacy
      raw-set_id format from before round-3). ``parsed`` is None.
      Caller treats as alive (in-place upgrade safety).
    - ``"json"`` — payload parses as a JSON object. ``parsed`` is the
      dict. Caller validates fields and decides aliveness.
    - ``"malformed"`` — payload starts with ``{`` but is invalid JSON
      OR is JSON but not an object. ``parsed`` is None. Round-4:
      caller MUST treat as dead — malformed-but-claimed-JSON is a
      bug, not legacy.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ("missing", None)
    text = text.strip()
    if not text:
        return ("missing", None)
    if not text.startswith("{"):
        return ("legacy", None)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ("malformed", None)
    if not isinstance(data, dict):
        return ("malformed", None)
    return ("json", data)


def _marker_alive_now(payload: Optional[dict]) -> bool:
    """Return True iff the marker described by ``payload`` is alive.

    ``payload`` is what ``_read_marker_payload`` returned; ``None`` is
    treated as alive (legacy raw-set_id marker — fail-open per the
    in-place-upgrade contract).

    PR #25 round-4 finding #2: a malformed-but-JSON-shaped marker
    (starts with ``{``, parses as a dict, but missing or invalid
    ``pid``) is a BUG, not legacy compatibility. Treat as DEAD so a
    stray malformed marker can't wedge the carve-out forever.

    For valid JSON markers we layer three liveness checks:

    1. ``os.kill(pid, 0)`` — handles SIGKILL / host-OOM (the canonical
       round-3 case).
    2. ``/proc/<pid>/stat`` field 22 cross-check — defends against PID
       reuse on Linux. If the marker stamped a ``proc_starttime`` and
       the live process at that pid has a different start time, the pid
       was reused → treat as dead.
    3. TTL on ``start_ts`` (``MAX_REFINE_DURATION_NS``) — bounds the
       damage on non-Linux hosts (no /proc) AND on cross-boot stale
       markers. Any negative elapsed value is impossible within one
       boot's monotonic clock contract, so it is stale/corrupt enough to
       retire immediately.

    Any check that says DEAD wins.
    """
    if payload is None:
        return True
    pid = payload.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        # JSON shape but no usable pid → bug/corruption, not legacy.
        # Treat as DEAD so the carve-out doesn't widen forever.
        return False
    if not _pid_is_alive(pid):
        return False
    stamped_starttime = payload.get("proc_starttime")
    if isinstance(stamped_starttime, int):
        live_starttime = _read_proc_starttime(pid)
        if live_starttime is not None and live_starttime != stamped_starttime:
            # PID was reused — different process now occupies that pid.
            return False
    start_ts = payload.get("start_ts")
    if isinstance(start_ts, int):
        elapsed = time.monotonic_ns() - start_ts
        if elapsed > MAX_REFINE_DURATION_NS or elapsed < 0:
            # TTL exceeded (or marker from a prior boot whose
            # monotonic_ns is in the future relative to ours).
            return False
    return True


def _read_marker_refine_idx(path: Path) -> tuple[str, Optional[int]]:
    """Return ``(kind, refine_idx)`` for marker ``path``.

    ``kind`` is the ``_classify_marker`` kind. Keeping it lets clear
    callers distinguish a legacy marker (safe to clear after consuming
    an orphan) from a transient read miss (fail closed and retry later).
    """
    kind, data = _classify_marker(path)
    if kind != "json" or data is None:
        return (kind, None)
    idx = data.get("refine_idx")
    if isinstance(idx, int):
        return (kind, idx)
    return (kind, None)


def _pid_is_alive(pid: int) -> bool:
    """Return True iff ``pid`` is a live process on this host.

    POSIX ``os.kill(pid, 0)`` raises ``ProcessLookupError`` for dead
    pids and ``PermissionError`` for live pids we don't own. Either
    way, anything that ISN'T ``ProcessLookupError`` means a process by
    that id exists, so we treat ``PermissionError`` as alive (fail
    open — better to keep an unrelated runner's carve-out than to drop
    a real one).
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Unexpected errno (e.g., EINVAL on signal=0 is a kernel bug);
        # fail open so we don't silently widen the round-2 invariant
        # the wrong way.
        return True
    return True


def mark_refine_in_flight(
    workdir: Path, set_id: str, *, refine_idx: Optional[int] = None,
) -> None:
    """Write the per-``set_id`` in-flight marker into ``workdir``.

    Idempotent: overwriting an existing marker is fine. Called from
    each runner's ``_refine_locked`` BEFORE the agent subprocess is
    spawned, so a crash anywhere from spawn through ``append_turn``
    leaves the marker on disk and lets ``recover_orphan_refines`` salvage
    the orphaned chat bubble on the next page-load.

    The marker payload is a JSON object with the writing process's pid,
    monotonic + ``/proc/self/stat`` timestamps, the ``set_id``, and
    (PR #25 round-4 finding #1) the ``refine_idx`` this marker is bound
    to. See ``_marker_payload`` for the on-disk shape.

    Readers use the pid + ``proc_starttime`` (Linux) + monotonic TTL
    triple to ignore markers whose owning process has died (host OOM /
    SIGKILL / power loss between mark and finally) AND markers whose
    pid was reused by an unrelated long-lived process. Recovery uses
    ``refine_idx`` to refuse clearing the marker when the consumed
    orphan's index doesn't match — so an older same-set orphan can't
    clear the current active runner's marker.

    Validates ``set_id`` shape (8 lowercase hex chars) so callers
    cannot inject path-traversal via the marker filename.

    ``refine_idx`` is optional only for legacy/test code paths; all
    real runner callers pass the ``expected_n`` they computed via
    ``_next_refine_index``. When ``refine_idx is None``, recovery
    falls back to the round-3 behaviour (clear after first
    consumption) — safe but loses the round-4 protection against
    older same-set orphans clearing the current marker.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    set_id = _validated_set_id(set_id)
    atomic_write_text(
        _inflight_marker_path(workdir, set_id),
        _marker_payload(set_id, refine_idx=refine_idx),
    )


def clear_refine_in_flight(workdir: Path, set_id: str) -> None:
    """Remove the per-``set_id`` in-flight marker.

    Called from a ``finally`` after ``append_turn`` succeeds, and also
    by ``chat_log.recover_orphan_refines`` (via
    ``clear_refine_in_flight_for_idx``) after consuming an orphan
    artifact pair. Defensive: a missing marker is not an error — we
    may be running after a partial crash that already lost the file,
    or after a sibling caller (recovery vs. runner finally) already
    cleared it. We never want a cleanup-side failure to mask the
    actual turn outcome.
    """
    set_id = _validated_set_id(set_id)
    try:
        _inflight_marker_path(workdir, set_id).unlink()
    except FileNotFoundError:
        return
    except OSError:
        # Best-effort cleanup; a stale marker only widens the carve-out
        # window slightly, doesn't corrupt anything.
        return


def clear_refine_in_flight_for_idx(
    workdir: Path, set_id: str, refine_idx: int,
) -> bool:
    """Clear the marker iff its stamped ``refine_idx`` matches.

    Returns ``True`` if a marker was cleared (or the marker carries no
    ``refine_idx`` stamp — legacy / pre-round-4 marker, in which case
    we fall back to round-3 behaviour and clear unconditionally so the
    permanent-bypass invariant is preserved). Returns ``False`` if the
    on-disk marker has a different ``refine_idx`` (the active runner
    owns it — must NOT be cleared) or if no marker exists.

    PR #25 round-4 finding #1: ``recover_orphan_refines`` calls this
    instead of plain ``clear_refine_in_flight`` so an older same-set
    orphan can't clear the current active runner's marker. The
    canonical failure path (without this guard): user clicks ship →
    status="shipped" → user starts another refine → runner stamps
    marker for ``refine_idx=N`` → meanwhile, recovery sees an orphan
    pair from a prior crashed turn (same set_id, ``refine_idx=N-1``),
    consumes it, and clears the live runner's marker. The active
    runner crashes mid-render; its ``finally`` clear is a no-op
    (already cleared). Next page-load recovery: the active runner's
    now-orphaned ``refine_N`` artifacts are NOT carved out → user
    loses the just-completed refine.
    """
    set_id = _validated_set_id(set_id)
    path = _inflight_marker_path(workdir, set_id)
    if not path.exists():
        return False
    kind, stamped_idx = _read_marker_refine_idx(path)
    if kind in {"missing", "malformed"}:
        # Missing may be a transient read/rename window; malformed is a
        # bug. In both cases, refuse to clear here rather than treating
        # them like legacy no-index markers.
        return False
    if stamped_idx is not None and stamped_idx != refine_idx:
        # Active runner owns a different refine_idx — leave its marker
        # alone. Recovery just consumed an OLDER same-set orphan, not
        # the current runner's in-flight artifacts.
        return False
    # Either the marker stamps our exact refine_idx, OR it's a legacy /
    # pre-round-4 marker with no refine_idx field. Claim the file with
    # an atomic rename before unlinking so a concurrent writer cannot be
    # removed after our check.
    claimed = path.with_name(
        f"{path.name}.clearing.{os.getpid()}.{time.monotonic_ns()}"
    )
    try:
        path.replace(claimed)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    kind2, stamped_idx2 = _read_marker_refine_idx(claimed)
    should_clear = (
        kind2 == "legacy"
        or (kind2 == "json" and stamped_idx2 is None)
        or (kind2 == "json" and stamped_idx2 == refine_idx)
    )
    if should_clear:
        try:
            claimed.unlink()
        except OSError:
            return False
        return True
    # Rollback: restore the marker we claimed without clobbering a
    # concurrent writer. Using ``os.link`` + ``unlink`` (instead of
    # ``claimed.replace(path)``) makes the link step fail with
    # ``FileExistsError`` when another process has stamped a fresh
    # marker for the same set_id between our check and now — that
    # writer wins, our claim is dropped (PR #26 round-1 P3).
    # ``pathlib.Path.replace`` would have silently overwritten their
    # marker. The per-process ``threading.Lock`` ordinarily prevents
    # this within one runner; the multi-process deployment topology
    # is what surfaces it.
    try:
        os.link(claimed, path)
    except FileExistsError:
        # Concurrent writer won; drop our claim.
        pass
    except OSError:
        # link can fail on cross-device or unsupported FS; fall back
        # to replace as a last resort. The window is small enough
        # that the original clobber risk is the lesser evil here vs.
        # losing the marker entirely.
        try:
            claimed.replace(path)
        except OSError:
            pass
        return False
    try:
        claimed.unlink()
    except OSError:
        pass
    return False


def _gc_clearing_sidecars(workdir: Path) -> None:
    """Best-effort GC of orphaned ``.clearing.<pid>.<ns>`` sidecars.

    PR #26 round-1 advisory (pr-coherence): if a process crashes between
    the ``path.replace(claimed)`` step in
    ``clear_refine_in_flight_for_idx`` and either the ``unlink`` or the
    rollback ``link``, the ``.clearing.<pid>.<ns>`` sidecar is orphaned
    forever. The ``_SET_ID_RE`` filter in
    ``active_refine_in_flight_set_ids`` already prevents these from
    widening the carve-out, but the dot-prefix design otherwise tries
    to keep workdirs clean.

    GC policy: parse ``pid`` from the filename suffix; if the process
    is no longer alive, unlink the orphan. Living-pid sidecars are left
    alone — their owning ``clear_refine_in_flight_for_idx`` call is
    presumably mid-flight and will clean up on its own. Best-effort:
    any error swallowed (the carve-out scan must never raise).
    """
    try:
        candidates = list(workdir.glob(f"{_INFLIGHT_PREFIX}*.clearing.*"))
    except OSError:
        return
    for p in candidates:
        # Filename shape: .refine_inflight_<sid>.clearing.<pid>.<ns>
        # — extract <pid> by partitioning on `.clearing.` and taking
        # the first dotted segment of the tail.
        _, _, tail = p.name.partition(".clearing.")
        if not tail:
            continue
        pid_str, _, _ = tail.partition(".")
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if pid <= 0 or _pid_is_alive(pid):
            continue
        try:
            p.unlink()
        except OSError:
            continue


def active_refine_in_flight_set_ids(workdir: Path) -> set[str]:
    """Return the set of ``set_id`` values with a LIVE in-flight marker.

    Glob is bounded by the on-disk file count and runs only on terminal
    runs (gated by the carve-out caller), so the cost is negligible.

    Filters out:

    - ``.tmp`` rename intermediates from in-flight ``atomic_write_text``
      calls (the glob pattern would otherwise match
      ``.refine_inflight_<sid>.tmp`` during the rename window). Doesn't
      cause false-positive recovery (the synthetic ``"<sid>.tmp"`` set
      element couldn't match a ``compute_set_id`` output) but pollutes
      the returned set with semantically-wrong values.
    - ``.clearing.<pid>.<ns>`` rename intermediates from
      ``clear_refine_in_flight_for_idx``'s atomic-claim path (PR #26
      round-1 advisory). Best-effort GC of these orphans (when their
      owning pid is dead) is folded into this scan via
      ``_gc_clearing_sidecars``.
    - Markers whose stamped pid is no longer alive — covers SIGKILL /
      host-OOM (round-3) AND PID reuse / cross-boot stale (round-4).
      See ``_marker_alive_now``. Treats markers without a JSON envelope
      (legacy / raw set_id payload) as alive so an in-place upgrade
      across releases doesn't drop carve-outs for runs already in
      flight.
    - Markers whose JSON envelope parses but is missing / has-invalid
      ``pid`` (PR #25 round-4: malformed-but-JSON-shaped is a BUG, not
      legacy compat — treat as DEAD).
    - Marker filenames whose suffix isn't a valid ``set_id`` shape
      (defense in depth — should never happen given
      ``mark_refine_in_flight`` validates; also catches the
      ``.clearing.*`` rename intermediates whose suffix is
      ``<sid>.clearing.<pid>.<ns>``).
    """
    _gc_clearing_sidecars(workdir)
    out: set[str] = set()
    try:
        candidates = list(workdir.glob(f"{_INFLIGHT_PREFIX}*"))
    except OSError:
        return out
    for p in candidates:
        if not p.is_file():
            continue
        if p.name.endswith(".tmp"):
            # Rename intermediate from atomic_write_text.
            continue
        if ".clearing." in p.name:
            # Atomic-claim rename intermediate from
            # clear_refine_in_flight_for_idx; GC handled above.
            continue
        sid = p.name[len(_INFLIGHT_PREFIX):]
        if not _SET_ID_RE.match(sid):
            continue
        kind, payload = _classify_marker(p)
        if kind == "malformed":
            # PR #25 round-4 advisory: malformed-but-claimed-JSON is a
            # bug or manual edit, not legacy compatibility. Treat as
            # DEAD so a stray marker can't widen the carve-out forever.
            continue
        if kind == "legacy" or kind == "missing":
            # Pre-round-3 marker (raw set_id payload) OR transient
            # read error — fail open and keep the carve-out so an
            # in-place upgrade across releases doesn't drop runs
            # already in flight.
            out.add(sid)
            continue
        # kind == "json" — apply the full liveness check.
        if not _marker_alive_now(payload):
            continue
        out.add(sid)
    return out


# ───────────────────────── refine index reservation ───────────────────


def _parse_refine_index_from_json_name(name: str) -> Optional[int]:
    if not (name.startswith("refine_") and name.endswith(".json")):
        return None
    try:
        return int(name[len("refine_"):-len(".json")])
    except ValueError:
        return None


def _parse_refine_index_from_reservation_name(name: str) -> Optional[int]:
    if not name.startswith(_REFINE_RESERVATION_PREFIX):
        return None
    raw = name[len(_REFINE_RESERVATION_PREFIX):]
    try:
        return int(raw)
    except ValueError:
        return None


def _reservation_is_alive(path: Path) -> bool:
    """Return True iff the reservation at ``path`` is owned by a live process.

    PR #26 round-1 P3: reservation files leak when the owning runner is
    SIGKILL'd between ``reserve_refine_index`` and the runner's
    ``finally`` clear. Without filtering, ``next_refine_index`` would
    advance monotonically over the dead reservations forever.

    PR #26 round-2 P3 (codex+opus): a SIGKILL between ``os.open(...
    O_EXCL...)`` (a 0-byte reservation file is now on disk) and the
    payload ``fp.write(payload)`` flush leaves a half-written
    reservation: empty, or JSON-shaped-but-malformed, or JSON-shaped
    with no ``pid`` field. Round-1's "JSON-shape ⇒ alive" classifier
    re-introduces the same SIGKILL leak the round-1 fix #4 closed for
    fully-written reservations, just in a smaller window (between
    ``O_EXCL`` and the flush). Mirror ``_marker_alive_now``'s
    discrimination: a JSON-shaped-but-malformed or missing-``pid``
    reservation is a BUG / half-written corpse, not legacy
    compatibility — treat as DEAD so ``next_refine_index`` can collect
    the slot's GC.

    PR #26 round-3 P1 (codex with reproducer): the round-2 "empty ⇒
    DEAD" branch is correct in isolation, but the round-2 publication
    path (open canonical with ``O_EXCL`` then ``fp.write(payload)``)
    has a live pre-flush window where a healthy allocator's canonical
    file is on disk under its final name and empty. A concurrent
    scanner reading that file would classify it DEAD and unlink it,
    letting another runner re-claim the same slot. Round-3 fixes
    this at the publication site by switching to stage-then-link
    (write payload to ``.refine_reserved_NNN.tmp.<pid>.<ns>``, then
    ``os.link`` into the canonical name). The classifier remains as
    written: an empty canonical reservation is now unambiguously
    legacy / crash residue from older code paths or pre-allocation
    buggy versions, never the in-flight intermediate of a healthy
    round-3 allocator.

    PR #26 round-3 advisory (opus): non-UTF-8 bytes under the
    canonical name (disk corruption, buggy out-of-band writer) raise
    ``UnicodeDecodeError`` which is a ``ValueError`` subclass and
    not caught by ``except OSError``. Round-3 catches it and treats
    decode failure as DEAD, mirroring the JSON-malformed branch.

    Layered reads:

    1. ``OSError`` other than ``FileNotFoundError`` (transient EIO,
       EACCES on the read) → fail OPEN (treat as alive). This is the
       only "wrongly-alive" carve-out left; the cost of a false-alive
       on EIO is one wasted slot until the next scan.
    2. ``FileNotFoundError`` on the read → reservation vanished out
       from under us; it is unambiguously gone, treat as DEAD so the
       caller doesn't burn a slot on it. (The dead-reservation GC's
       follow-up unlink is then a tolerated FileNotFoundError.)
    3. ``UnicodeDecodeError`` on the read → non-UTF-8 bytes under the
       canonical name. The PR #26 payload is always JSON-encoded
       UTF-8, so this is corruption, not a legacy format we have to
       respect. Treat as DEAD.
    4. Empty file (legacy / pre-round-3 crash residue),
       non-JSON-shaped (no leading ``{``), JSON-shaped but
       unparseable, parsed but not a dict, or dict missing/invalid
       ``pid`` → all DEAD. The round-3 stage-then-link publication
       path never produces an empty canonical file in healthy
       operation.
    5. ``pid`` field present and dead → reservation is dead.
    6. ``proc_starttime`` mismatch → pid was reused → dead.

    Reservations have no monotonic-TTL fallback (unlike markers) — a
    long-running refine can legitimately hold a reservation longer than
    ``MAX_REFINE_DURATION_NS`` if the agent itself runs long, and the
    only cost of a falsely-alive reservation is one wasted index slot
    (the ``O_EXCL`` claim on the SAME slot would loop and pick a
    different N), not a wedged carve-out.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # File vanished mid-scan; not alive.
        return False
    except UnicodeDecodeError:
        # PR #26 round-3 advisory (opus): the canonical reservation
        # payload is always JSON UTF-8, but disk corruption or a
        # buggy out-of-band writer could leave non-UTF-8 bytes here.
        # ``UnicodeDecodeError`` is a ``ValueError`` subclass and
        # would NOT have been caught by the bare ``except OSError``,
        # so it would escape ``_reservation_is_alive`` and propagate
        # up through ``next_refine_index`` → ``reserve_refine_index``
        # → kill the request. Treat as DEAD (same shape as the
        # JSON-malformed branch below): the slot is reclaimable and
        # the GC unlink in ``next_refine_index`` will clear it.
        return False
    except OSError:
        # Transient read failure (EIO, transient EACCES). Fail open
        # so a flaky disk doesn't drop a real owner's slot.
        return True
    text = text.strip()
    if not text:
        # Legacy / crash-residue empty reservation (e.g. older code
        # that opened the canonical file with ``O_EXCL`` and was
        # SIGKILL'd before flushing). The PR #26 round-3 publication
        # path stages the payload first and ``os.link``s into the
        # canonical name, so a healthy in-flight allocator never
        # exposes an empty canonical file. An empty file under the
        # canonical name is therefore unambiguously dead — the slot
        # can be GC'd.
        return False
    if not text.startswith("{"):
        # Non-JSON content. PR-#26 reservations are always JSON; any
        # other shape is corruption, not a legacy format we have to
        # respect.
        return False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # JSON-shaped but malformed: half-written or disk-corrupted.
        # Mirrors ``_marker_alive_now``'s "JSON-shape but bad ⇒ DEAD".
        return False
    if not isinstance(data, dict):
        return False
    pid = data.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        # JSON dict but no usable pid → bug/corruption, not legacy.
        # Treat as DEAD so the slot can be reclaimed.
        return False
    if not _pid_is_alive(pid):
        return False
    stamped_starttime = data.get("proc_starttime")
    if isinstance(stamped_starttime, int):
        live_starttime = _read_proc_starttime(pid)
        if live_starttime is not None and live_starttime != stamped_starttime:
            return False
    return True


def next_refine_index(workdir: Path) -> int:
    """Return the next workdir-global ``refine_NNN`` index.

    Counts both completed ``refine_*.json`` files and hidden reservation
    files. Reservations are what make concurrent refines on different
    ``set_id`` values allocate distinct artifact names before either
    agent has produced its final JSON.

    PR #26 round-1 P3: reservations stamped by a process that has since
    died (host OOM / SIGKILL between ``reserve_refine_index`` and the
    runner's ``finally`` clear) are filtered via ``_reservation_is_alive``
    so they don't inflate the global counter forever. Mirrors the
    in-flight marker liveness pass in ``active_refine_in_flight_set_ids``.

    PR #26 round-4 P3 (pr-coherence): also folds in a best-effort sweep
    of ``.refine_reserved_NNN.tmp.<pid>.<ns>`` staging-file orphans
    (symmetric to ``_gc_clearing_sidecars`` — see
    ``_gc_reservation_staging_orphans``). The reservation parser
    already ignores them for index counting, so this is purely a
    workdir-cleanliness sweep.
    """
    _gc_reservation_staging_orphans(workdir)
    indices: list[int] = []
    try:
        jsons = list(workdir.glob("refine_*.json"))
    except OSError:
        jsons = []
    for p in jsons:
        idx = _parse_refine_index_from_json_name(p.name)
        if idx is not None:
            indices.append(idx)
    try:
        reservations = list(workdir.glob(f"{_REFINE_RESERVATION_PREFIX}*"))
    except OSError:
        reservations = []
    for p in reservations:
        idx = _parse_refine_index_from_reservation_name(p.name)
        if idx is None:
            continue
        if not _reservation_is_alive(p):
            # Stale reservation from a SIGKILL'd runner; collect the
            # garbage best-effort and skip it. Failing to unlink is
            # fine — another concurrent scanner won the race to
            # unlink first; the slot is already free, and either
            # scanner correctly skips it for index counting on the
            # next pass.
            try:
                p.unlink()
            except OSError:
                pass
            continue
        indices.append(idx)
    return (max(indices) if indices else 0) + 1


def _gc_reservation_staging_orphans(workdir: Path) -> None:
    """Best-effort GC of orphaned ``.refine_reserved_NNN.tmp.<pid>.<ns>`` files.

    PR #26 round-4 P3 (pr-coherence): the round-3 ``reserve_refine_index``
    publication path stages payloads to ``.refine_reserved_NNN.tmp.<pid>.<ns>``
    before ``os.link``-ing into the canonical name. Three failure shapes
    can leak the staging file:

    - SIGKILL between ``os.open(O_EXCL)`` of the staging path and the
      ``staging.unlink()`` cleanup arm of the publication block.
    - ``os.link`` raises ``OSError`` on a cross-FS workdir (``EXDEV``)
      and the ``except OSError`` cleanup arm itself fails (e.g. EIO on
      the same disk).
    - The post-publish ``staging.unlink()`` (line 1118) fails silently.

    The reservation parser already filters these orphans out for index
    counting (their suffix doesn't parse as ``int``), so they are inert
    correctness-wise. They merely clutter long-lived workdirs. This
    sweep mirrors ``_gc_clearing_sidecars``: parse the ``<pid>`` from
    the suffix, unlink only when that pid is no longer alive. Living-pid
    staging files are left alone — their owning ``reserve_refine_index``
    call may still be mid-flight and will clean up on its own.
    """
    try:
        candidates = list(
            workdir.glob(f"{_REFINE_RESERVATION_PREFIX}*.tmp.*")
        )
    except OSError:
        return
    for p in candidates:
        # Filename shape: .refine_reserved_NNN.tmp.<pid>.<ns>
        # — extract <pid> by partitioning on `.tmp.` and taking the
        # first dotted segment of the tail.
        _, _, tail = p.name.partition(".tmp.")
        if not tail:
            continue
        pid_str, _, _ = tail.partition(".")
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if pid <= 0 or _pid_is_alive(pid):
            continue
        try:
            p.unlink()
        except OSError:
            continue


def reserve_refine_index(workdir: Path, set_id: str) -> int:
    """Atomically reserve and return a workdir-global ``refine_NNN`` index.

    Per-set runner locks prevent duplicate POSTs for the same baseline
    set, but two different ``set_id`` values can refine concurrently in
    the same workdir. This reservation file closes the cross-set race by
    publishing a fully-written reservation under the canonical
    ``.refine_reserved_NNN`` name in a single atomic step before the
    agent writes artifacts.

    PR #26 round-1 P1 (codex critical): there is a separate race window
    between ``next_refine_index``'s scan and the canonical claim. If
    another runner finalizes between the JSON-glob and the
    reservation-glob (writes ``refine_NNN.json`` then unlinks
    ``.refine_reserved_NNN``), this scan will miss BOTH and the
    claim on N can succeed even though N is already used by a finalized
    output. After claiming, we recheck whether ``refine_NNN.json``
    materialized in the meantime; if so, release the reservation and
    retry with a higher N.

    PR #26 round-3 P1 (codex with reproducer): the original "open
    canonical with ``O_EXCL`` then ``fp.write(payload)``" sequence
    exposes a window where the canonical file is on-disk under its
    final name but empty. Round-2's tightened ``_reservation_is_alive``
    correctly classifies an empty reservation file as DEAD (since a
    SIGKILL'd allocator can leak that exact shape), but on the live
    pre-flush path the same shape is the in-flight intermediate state
    of a healthy allocator. A concurrent scanner would then unlink a
    live allocator's just-claimed canonical slot, recreating the
    cross-set duplicate-allocation race that round-1 finding #1
    closed (codex repro: two ``set_id`` values both winning
    ``refine_idx=1``).

    Round-3 fix: stage the payload first into a per-attempt
    ``.refine_reserved_NNN.tmp.<pid>.<ns>`` file, then ``os.link`` that
    fully-written staging file into the canonical name. The canonical
    reservation is therefore non-empty the moment it is visible under
    its canonical name, mirroring this module's surrounding
    ``atomic_write_text`` invariant: never publish a partially-written
    sidecar under its canonical name. The empty-reservation ⇒ DEAD
    classification in ``_reservation_is_alive`` remains correct as a
    defense-in-depth signal for legacy / crash-residue files written
    by older code paths or pre-allocation buggy versions; the new
    publication path no longer produces empty canonical files.

    The supported filesystem boundary remains as documented in the PR
    body: ``os.link`` requires a hardlink-supporting workdir
    filesystem (XFS / ext4). On filesystems without hardlink support
    (FAT, exFAT, some FUSE mounts) the call raises ``OSError`` and
    propagates to the SSE error path with the original I/O error
    class preserved.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    set_id = _validated_set_id(set_id)
    for _ in range(MAX_RESERVE_ATTEMPTS):
        idx = next_refine_index(workdir)
        path = workdir / f"{_REFINE_RESERVATION_PREFIX}{idx:03d}"
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "start_ts": time.monotonic_ns(),
                "proc_starttime": _read_proc_starttime(os.getpid()),
                "set_id": set_id,
                "refine_idx": idx,
            },
            sort_keys=True,
        ) + "\n"
        # Round-3 P1: write the payload into a uniquely-named staging
        # file before linking it into the canonical reservation slot.
        # The pid + monotonic_ns suffix makes name collisions
        # astronomically unlikely; ``O_EXCL`` on the staging path is
        # belt-and-braces. The staging name does not parse as a
        # reservation index (``int(...)`` over the ``NNN.tmp.<pid>.<ns>``
        # tail raises ``ValueError``), so concurrent
        # ``next_refine_index`` scans ignore it for index counting.
        staging = workdir / (
            f"{_REFINE_RESERVATION_PREFIX}{idx:03d}.tmp."
            f"{os.getpid()}.{time.monotonic_ns()}"
        )
        try:
            fd = os.open(
                staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
            )
        except FileExistsError:
            # Vanishingly unlikely (would require a pid + ns collision
            # in the same workdir). Retry with a fresh staging name on
            # the next loop iteration; ``next_refine_index`` may yield
            # the same idx, but the new ns suffix will differ.
            continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                fp.write(payload)
                # PR #26 round-4 P3 (opus): force the payload to stable
                # storage BEFORE ``os.link`` publishes the canonical
                # name. Without the fsync, a power loss between the
                # user-buffer flush at ``fp.close`` and the ``os.link``
                # below can leave the canonical hardlink referencing an
                # inode whose contents have not yet been flushed —
                # post-recovery, that is the same "empty canonical
                # residue" shape round-2's ``_reservation_is_alive``
                # classifies as DEAD. The slot is still recoverable
                # (the GC pass collects it), but cleaner to publish
                # only durable bytes and avoid the recovery path
                # altogether.
                fp.flush()
                os.fsync(fp.fileno())
        except OSError:
            # Best-effort cleanup of the staging file we just opened
            # so a write or fsync failure (ENOSPC, EIO on flush) doesn't
            # leak it. The canonical name has not been published yet.
            try:
                staging.unlink()
            except OSError:
                pass
            raise
        # Atomic publication: link the fully-written staging file into
        # the canonical reservation name. ``os.link`` raises
        # ``FileExistsError`` when the canonical slot is already claimed
        # by another runner; in that case we drop the staging file and
        # retry with a fresh idx (``next_refine_index`` will pick a
        # higher one once the existing canonical is visible).
        try:
            os.link(staging, path)
        except FileExistsError:
            try:
                staging.unlink()
            except OSError:
                pass
            continue
        except OSError:
            # Hardlink unsupported (cross-FS, FAT/exFAT, some FUSE) or
            # other I/O failure. Drop the staging file and bubble the
            # error up — caller's per-(workdir, set_id) lock releases
            # in ``finally`` and the SSE error path surfaces the
            # original I/O class. The PR body's deploy notes document
            # the hardlink-supporting FS requirement.
            try:
                staging.unlink()
            except OSError:
                pass
            raise
        # Drop the now-redundant staging file; the canonical hardlink
        # retains the inode so the payload remains readable. Failure
        # here is not a correctness bug (the file is inert under the
        # reservation parser), only a clutter cost.
        try:
            staging.unlink()
        except OSError:
            pass
        # PR #26 round-1 P1 recheck: if a sibling runner finalized
        # ``refine_idx.json`` while we were between the scan and the
        # claim, drop the reservation and retry with a fresh idx.
        if (workdir / f"refine_{idx:03d}.json").exists():
            clear_refine_reservation(workdir, idx)
            continue
        return idx
    # PR #26 round-2 advisory: bounded loop guard. ``next_refine_index``
    # advances on every iteration in practice, so reaching the cap means
    # something pathological is happening (sustained adversarial
    # finalize traffic, allocator livelock, or an FS bug). Fail loud
    # instead of spinning forever — the caller's per-(workdir, set_id)
    # lock will release in the ``finally`` and the failure surfaces in
    # the SSE error path with the original I/O error class preserved.
    raise OSError(
        f"reserve_refine_index: failed to acquire a refine slot in "
        f"{MAX_RESERVE_ATTEMPTS} attempts in workdir {workdir!r}; "
        f"sustained allocator livelock or FS misbehavior — refusing "
        f"to spin further."
    )


def clear_refine_reservation(workdir: Path, refine_idx: int) -> None:
    """Best-effort cleanup for a previously reserved refine index."""
    try:
        (workdir / f"{_REFINE_RESERVATION_PREFIX}{refine_idx:03d}").unlink()
    except (FileNotFoundError, OSError):
        return


# ─────────────────────────── set_id derivation ───────────────────────


def compute_set_id(baseline_iters: list[int]) -> str:
    """Derive the ``set_id`` for a Phase-2 baseline-set chat session.

    ``set_id`` is the first 8 hex chars of SHA-1 over the canonical
    representation: comma-joined, sorted, deduplicated iter list.

    Properties (asserted in ``test_compute_set_id``):

    - **Order-independent**: ``[5,3,1]`` and ``[1,3,5]`` hash identically.
    - **Dedup-tolerant**: ``[1,3,3]`` and ``[1,3]`` hash identically.
    - **Deterministic**: identical input → identical output, always.

    Raises ``ValueError`` on empty input — at least one baseline iter
    is required to address a refine session.
    """
    if not baseline_iters:
        raise ValueError("compute_set_id requires at least one baseline iter")
    canonical = ",".join(str(i) for i in sorted(set(baseline_iters)))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]


# ─────────────────────── data placeholder marker ─────────────────────


# When the user submits a run without uploading a data file,
# ``figcopy_serve.create_run`` writes this exact text to
# ``inputs/data.txt`` so the runner can detect "no user data
# provided" and trigger the dedicated data-gen pass instead of letting
# the Drawer fabricate inline. The constant lives here so the server
# (producer) and ``codex.CodexRunner`` (consumer) reference a single
# source of truth — under the prior arrangement an edit to the
# producer string silently broke the consumer's substring-match and
# every run regressed to inline fabrication.
DATA_PLACEHOLDER_TEXT = (
    "# No data provided.\n"
    "# Please fabricate plausible numeric data whose shape and units\n"
    "# match the reference figure. Record the fabrication in data_echo.md.\n"
)


def is_data_placeholder(text: Optional[str]) -> bool:
    """Return True iff ``text`` is the run-create placeholder (exact
    match modulo trailing whitespace).

    Exact equality (not substring) so an agent-generated file that
    happens to contain the marker as a comment is not misclassified
    as the still-placeholder state.
    """
    if text is None:
        return False
    return text.strip() == DATA_PLACEHOLDER_TEXT.strip()


# ─────────────────────────── Runner Protocol ─────────────────────────


class Runner(Protocol):
    """Abstract backend that produces iter files into a figcopy workdir.

    Concrete impls drive whichever CLI / agent stack we have available
    (codex, claude, mock, …). The webui doesn't know — it just observes
    files appearing on disk + reads the status sidecar + subscribes to
    the event bus for live streaming.
    """

    def start(self, workdir: Path, *, prompt: str = "",
              max_iters: int = 5, auto: bool = False) -> str:
        """Stage a run; return the run id (``workdir.name``).

        Async — returns immediately. A background thread / subprocess
        produces iter files into ``workdir`` over time. Caller observes
        progress via the status sidecar + (Phase 3) the event bus.
        ``auto=True`` means the runner follows deterministic review actions
        without pausing until the Reviewer returns ``ship``, a real blocker
        occurs, or the hard ``max_iters`` Drawer cap is reached.
        """
        ...

    def status(self, workdir: Path) -> dict:
        """Return the current run state.

        Returns a dict with ``state`` ∈ {running, shipped, failed,
        cancelled, idle} and optionally ``current_iter: int``. Falls
        back to the on-disk sidecar if the runner has no in-memory
        record; if neither exists, returns ``{"state": "idle"}``.
        """
        ...

    def cancel(self, workdir: Path, *, slot: str = "iter") -> None:
        """Best-effort cancellation; idempotent.

        ``slot`` is one of:

        - ``"iter"`` — cancel the Step-1 iter loop
        - ``"refine:<set_id>"`` — cancel an in-flight Step-2 refine
          turn on the given baseline set

        Phase 2's signature was ``cancel(workdir)`` with no slot;
        Phase 3 adds the kwarg with a default for backward
        compatibility. Real backends use the slot to find the right
        subprocess in their per-(workdir, slot) registry.
        """
        ...

    def refine(self, workdir: Path, *, baseline_iters: list[int],
               message: Optional[str] = None,
               adjustments: Optional[dict] = None) -> dict:
        """Run one Step-2 turn over a baseline set.

        ``baseline_iters`` is the (1+ length) list of iter indices the
        user multi-selected as the refine context. The runner computes
        ``set_id = compute_set_id(baseline_iters)`` and uses
        ``(workdir, set_id)`` as the session key — successive calls
        with the same baseline set MUST share an agent session
        (multi-turn continuity). Different baseline sets MUST get
        distinct sessions.

        ``message`` is the user's natural-language turn.
        ``adjustments`` is the structured-control alternative
        (e.g. ``{"font.size": 15}``); when set, the runner is
        responsible for translating it to prose server-side
        (see ``adjustments_to_prose``) — the agent only ever sees
        natural language.

        Returns a dict with shape::

            {
              "image_url": "refine_NNN.png",   # relative; server prefixes
              "rcparams_delta": {...},          # what changed this turn
              "review": "...",                  # the agent's prose review
              "set_id": "<set_id>",             # echoed for client routing
              "seq": <int>                      # event-bus seq of refine_complete
            }

        Blocks until both ``refine_NNN.png`` and ``refine_NNN.json``
        land atomically in the workdir. If the agent retries internally
        within the turn (writes code, runs matplotlib, fixes errors,
        reruns), those attempts surface as tool-call events on the
        event bus — the runner only declares "done" when the final
        artifacts land. If the agent gives up, ``RefineFailed`` is
        raised (or the runner returns a failure-shaped dict; impls
        decide).
        """
        ...
