"""ClaudeRunner — real claude CLI backend.

Mirrors :class:`figcopy_runner.codex.CodexRunner` but drives Claude
Code's ``claude`` CLI (v2.1+). Same Runner Protocol surface, different
subprocess wire format.

Architecture differences vs CodexRunner:

- **Single subprocess per main loop turn**: the Claude skill at
  ``~/.claude/skills/figmirror/`` is designed so the CALLING
  thread is the orchestrator and uses Claude Code's native ``Task``
  tool to dispatch ``figure-preprocessor`` (Stage 0),
  ``figure-illustrator`` (Drawer), and ``figure-critic`` (Reviewer)
  subagents. The runner also performs a bounded Stage-0 preprocess pass
  before the main loop, so the main loop starts with a clean L1 crop. By
  contrast, CodexRunner's ``orchestrator-codex.md`` shells out a
  separate ``codex exec`` for the reviewer.

- **stream-json wire format**: Claude emits
  ``{"type":"system",...}`` / ``{"type":"assistant","message":{...}}``
  / ``{"type":"user","message":{...}}`` / ``{"type":"result",...}``
  events. We parse these into our :mod:`events` SessionEvent union
  the same way CodexRunner does for codex's ``item.completed`` shape.

- **Session resume**: ``claude --resume <session_id>`` continues an
  existing thread; we pin our own session UUID via
  ``--session-id <uuid>`` on turn-1 so refine() turn-2+ resume cleanly.

- **No native vision-as-tool-output bug**: Claude's API accepts
  ``image`` blocks in tool_result content, so the Reviewer subagent
  can see the rendered iter PNG just fine — no proxy workaround
  needed (unlike Codex via the copilot proxy).

The data-gen pass that CodexRunner runs before the orchestrator is
NOT replicated here yet; the Claude skill's Drawer fabricates inline
when ``inputs/data.txt`` is the placeholder. Adding a parallel
``_run_data_gen_pass`` is a future cleanup (same prompt template).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from . import _lifecycle, chat_log, event_bus, events
from .adjustments_to_prose import to_prose
# Reuse CodexRunner's quality-floor-aware finalize selector so both
# backends ship the SAME iter for a given on-disk audit set. (codex.py
# does not import claude.py, so there is no circular-import risk here.)
from .codex import _pick_finalize_iter
from .interface import (
    atomic_write_text,
    clear_refine_in_flight,
    clear_refine_reservation,
    compute_set_id,
    ensure_reference_raw,
    mark_refine_in_flight,
    next_refine_index,
    read_sessions,
    read_status_sidecar,
    reserve_refine_index,
    reviewer_protocol_failure_reason,
    write_sessions,
)
from .refine_completion import (
    salvage_refine_output_from_tmp,
    wait_for_refine_output_or_done,
)
from .refine_prompt import build_system_prompt


# Repo root — used for `uv run --project <repo>` and loading repo-side
# runner helpers. Agent subprocess cwd stays on the run workdir so
# skill dispatch exercises the installed user-level package and shell
# tools resolve relative paths inside the run.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Flags shared between start() and refine(). `--print` makes claude
# non-interactive; `--output-format stream-json` gives us the
# machine-readable event stream that the reader thread parses;
# `--verbose` is required alongside stream-json output.
# `--permission-mode bypassPermissions` skips per-tool approvals
# (the runs are sandboxed to their own workdir via --add-dir).
_BASE_FLAGS: list[str] = [
    "--print",
    "--output-format", "stream-json",
    "--verbose",
    "--permission-mode", "bypassPermissions",
]


def _uv_cmd(*args: str) -> list[str]:
    return ["uv", "run", "--project", str(_REPO_ROOT), *args]


def _uv_env() -> dict[str, str]:
    env = os.environ.copy()
    # Reason: same as codex.py `_uv_env()` — default UV_CACHE_DIR to a
    # per-repo path so first-use `uv run` wheel pulls land in a
    # predictable location instead of bloating $HOME/.cache on
    # shared/small-root hosts. Operators with their own preference can
    # export UV_CACHE_DIR themselves; setdefault preserves it.
    # `.artifacts/` is gitignored.
    env.setdefault("UV_CACHE_DIR", str(_REPO_ROOT / ".artifacts" / "uv-cache"))
    return env


# Refine turns are user-interactive and may spend time debugging render
# failures. By default, wait until the agent either writes the promised
# artifact pair or exits. Tests may monkeypatch a finite timeout to
# exercise failure handling.
REFINE_TURN_TIMEOUT_S: Optional[float] = None

# Stage-0 reference crop timeout. Mirrors CodexRunner.
_REFERENCE_PREPROCESS_TIMEOUT_S = 300.0


class RefineInFlight(RuntimeError):
    """Raised by refine() when a same-(workdir, set_id) turn is
    already running. Server translates to HTTP 409."""


class RefineFailed(RuntimeError):
    """Raised by refine() when the agent gave up / timed out without
    writing refine_NNN.{png,json}. Server translates to HTTP 500."""


class ClaudeRunner:
    """Real claude CLI backend.

    Mirrors CodexRunner's lock / state tables so the two backends
    expose identical concurrency behaviour to the server layer.
    """

    def __init__(self) -> None:
        self._refine_locks: dict[tuple[str, str], threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._sessions_locks: dict[str, threading.Lock] = {}
        self._sessions_locks_guard = threading.Lock()
        self._iter_state: dict[Path, dict] = {}
        self._iter_state_lock = threading.Lock()

    def _get_sessions_lock(self, workdir: Path) -> threading.Lock:
        key = str(workdir)
        with self._sessions_locks_guard:
            lock = self._sessions_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._sessions_locks[key] = lock
            return lock

    # ───── public Runner Protocol ─────────────────────────────────────

    def start(self, workdir: Path, *, prompt: str = "",
              max_iters: int = 6, auto: bool = False) -> str:
        """Kick off a Stage-1 run on the claude backend.

        Returns immediately. The actual ``claude --print`` subprocess
        is spawned from a background thread so the HTTP POST that
        called us can return inside the 200-ms budget.
        """
        workdir = workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        with self._iter_state_lock:
            prior = self._iter_state.get(workdir, {})
            if prior.get("state") == "running":
                print(
                    f"[claude-runner:{workdir.name}] start() called while "
                    f"a run is already in-flight (gen={prior.get('gen')}); "
                    f"ignoring",
                    file=sys.stderr,
                )
                return workdir.name
            gen = (prior.get("gen", 0) or 0) + 1
            self._iter_state[workdir] = {
                "state": "running",
                "current_iter": None,
                "gen": gen,
            }
        _write_status(workdir, state="running", current_iter=None)

        threading.Thread(
            target=self._stage1_orchestrate,
            args=(workdir, prompt, max_iters, auto, gen),
            daemon=True,
            name=f"claude-stage1:{workdir.name}",
        ).start()
        return workdir.name

    def status(self, workdir: Path) -> dict:
        workdir = workdir.resolve()
        with self._iter_state_lock:
            entry = self._iter_state.get(workdir)
        if entry:
            out = {"state": entry["state"]}
            if entry.get("current_iter") is not None:
                out["current_iter"] = entry["current_iter"]
            return out
        return read_status_sidecar(workdir) or {"state": "idle"}

    def cancel(self, workdir: Path, *, slot: str = "iter") -> None:
        workdir = workdir.resolve()
        _lifecycle.terminate_slot(workdir, slot)
        if slot == "iter":
            with self._iter_state_lock:
                if workdir in self._iter_state:
                    self._iter_state[workdir]["state"] = "cancelled"
            _write_status(workdir, state="cancelled", current_iter=None)

    def refine(self, workdir: Path, *,
               baseline_iters: list[int],
               message: Optional[str] = None,
               adjustments: Optional[dict] = None) -> dict:
        if not baseline_iters:
            raise ValueError(
                "ClaudeRunner.refine: baseline_iters must be non-empty"
            )
        workdir = workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        set_id = compute_set_id(baseline_iters)
        slot = f"refine:{set_id}"

        lock = self._get_refine_lock(workdir, set_id)
        if not lock.acquire(blocking=False):
            raise RefineInFlight(
                f"refine on set_id={set_id} already in progress"
            )
        expected_n: Optional[int] = None
        try:
            # Reserve the refine_NNN index this turn will write. It's
            # consumed twice: by ``mark_refine_in_flight`` (PR #25
            # round-4 finding #1 — the marker payload binds the
            # carve-out to THIS index, so an older same-set orphan
            # can't clear our marker through recovery), and by
            # ``_refine_locked`` for the artifact-pair filenames.
            # Reserving here also prevents concurrent different-set
            # refines in the same workdir from both choosing N before
            # either has landed its final refine_NNN.json.
            expected_n = reserve_refine_index(workdir, set_id)
            # Stamp the per-``set_id`` in-flight marker BEFORE the
            # subprocess spawn so ``chat_log.recover_orphan_refines``
            # can carve a hole in the terminal-status gate for the
            # canonical Step-2 user flow on a shipped run. Cleared in
            # the finally below, AFTER ``_refine_locked`` returns
            # (i.e., AFTER its ``chat_log.append_turn`` call) — see
            # PR #25 round-2 finding #1 and
            # ``interface.mark_refine_in_flight``'s docstring.
            #
            # PR #25 round-3 finding (lock-leak): the marker write
            # MUST live INSIDE the try block. ``mark_refine_in_flight``
            # invokes ``atomic_write_text`` which can raise ``OSError``
            # (ENOSPC / EROFS / EACCES / quota / read-only bind-mount).
            # If it raised before the try, ``lock.release()`` would
            # never run, permanently wedging this set_id with
            # ``RefineInFlight`` until process restart.
            mark_refine_in_flight(workdir, set_id, refine_idx=expected_n)
            return self._refine_locked(
                workdir, set_id, slot, baseline_iters,
                message, adjustments, expected_n=expected_n,
            )
        finally:
            # PR #25 round-4 advisory hardening: release the lock
            # FIRST, then clear the marker. ``clear_refine_in_flight``
            # validates ``set_id`` and CAN raise ``ValueError`` on a
            # malformed value — impossible today (all callers pass
            # ``compute_set_id`` output, always 8-hex), but if a
            # future caller wires arbitrary input through this
            # surface the clear's exception would mask the original
            # exception AND skip ``lock.release()``, recreating in
            # miniature the lock-leak shape round-3 just closed.
            # Releasing first guarantees the lock never leaks
            # regardless of clear-side bugs. ``clear_refine_in_flight``
            # is idempotent (a missing marker is not an error), so
            # calling it after a failed ``mark_refine_in_flight`` is
            # safe.
            lock.release()
            if expected_n is not None:
                clear_refine_reservation(workdir, expected_n)
            clear_refine_in_flight(workdir, set_id)

    def _get_refine_lock(self, workdir: Path, set_id: str) -> threading.Lock:
        key = (str(workdir), set_id)
        with self._locks_guard:
            lock = self._refine_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._refine_locks[key] = lock
            return lock

    def _own_generation(self, workdir: Path, my_gen: int) -> bool:
        with self._iter_state_lock:
            cur = self._iter_state.get(workdir, {})
        return cur.get("gen") == my_gen

    # ───── stage-1 orchestrator ───────────────────────────────────────

    def _stage1_orchestrate(self, workdir: Path, prompt: str,
                            max_iters: int, auto: bool,
                            my_gen: int) -> None:
        bus = event_bus.get_bus()
        bus.publish(workdir, "iter", events.TurnStartEvent(data={
            "set_id": None,
            "max_iters": max_iters,
            "auto": auto,
        }))
        if not self._own_generation(workdir, my_gen):
            bus.publish(workdir, "iter", events.TurnEndEvent(data={
                "status": "cancelled",
                "reason": "superseded by newer run",
            }))
            return
        bus.publish(workdir, "iter", events.TextEvent(data={
            "text": (
                "Preparing the reference crop before the "
                "Drawer/Reviewer loop…"
            ),
            "is_partial": False,
        }))
        ok = self._run_reference_preprocess_pass(workdir)
        with self._iter_state_lock:
            cur = self._iter_state.get(workdir, {})
        if cur.get("state") == "cancelled":
            bus.publish(workdir, "iter", events.TurnEndEvent(data={
                "status": "cancelled",
                "reason": "cancelled",
            }))
            return
        if cur.get("gen") != my_gen:
            bus.publish(workdir, "iter", events.TurnEndEvent(data={
                "status": "cancelled",
                "reason": "superseded by newer run",
            }))
            return
        if not ok:
            with self._iter_state_lock:
                cur2 = self._iter_state.get(workdir, {})
                if cur2.get("gen") == my_gen:
                    self._iter_state[workdir]["state"] = "failed"
            _write_status(
                workdir, state="failed", current_iter=None,
                reason="reference preprocessing pass failed",
            )
            bus.publish(workdir, "iter", events.TurnEndEvent(data={
                "status": "failed",
                "reason": "reference preprocessing pass failed",
            }))
            return
        bus.publish(workdir, "iter", events.TextEvent(data={
            "text": "Reference crop ready.",
            "is_partial": False,
        }))
        if not self._own_generation(workdir, my_gen):
            bus.publish(workdir, "iter", events.TurnEndEvent(data={
                "status": "cancelled",
                "reason": "superseded by newer run",
            }))
            return
        self._spawn_orchestrator(workdir, prompt, max_iters, auto)

    def _run_reference_preprocess_pass(self, workdir: Path) -> bool:
        """Run the figure-preprocessor prompt before the main loop."""
        raw = ensure_reference_raw(workdir)
        clean = workdir / "inputs" / "reference_clean.png"
        check = workdir / "inputs" / "reference_crop_check.png"
        report = workdir / "inputs" / "reference_crop_report.md"
        if raw is None:
            print(
                f"[claude-runner:{workdir.name}] reference-preprocess: "
                "no reference image found",
                file=sys.stderr,
            )
            return False

        try:
            prompt = _load_reference_preprocessor_prompt()
        except Exception as exc:
            print(
                f"[claude-runner:{workdir.name}] reference-preprocess: "
                f"prompt load failed: {exc}",
                file=sys.stderr,
            )
            return False

        full_prompt = (
            "Use the FigMirror Stage-0 reference preprocessor for this task.\n"
            "If you inspect FigMirror SKILL.md, bundled references, or agents, use "
            "these user-level Claude Code install paths and resolve relative "
            "paths under them:\n\n"
            f"    FIGMIRROR_SKILL_DIR = {_claude_skill_dir_for_prompt()}\n"
            f"    FIGMIRROR_AGENTS_DIR = {_claude_agents_dir_for_prompt()}\n\n"
            f"{prompt}\n\n---\n\n"
            f"Run Stage 0 reference preprocessing in this workdir:\n"
            f"{workdir}\n\n"
            "Use `inputs/reference_raw.png` as the raw upload. Write "
            "`inputs/reference_clean.png`, `inputs/reference_crop_check.png`, "
            "and `inputs/reference_crop_report.md`."
        )

        cmd = _uv_cmd(
            "claude", *_BASE_FLAGS,
            "--add-dir", str(workdir),
        )
        log_fp = open(workdir / "agent_reference_preprocess.log", "ab", buffering=0)
        debug_stdout_fp = open(
            workdir / "claude_stdout_reference_preprocess.jsonl",
            "ab", buffering=0,
        )
        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            env=_uv_env(),
            stdout=subprocess.PIPE,
            stderr=log_fp,
            stdin=subprocess.PIPE,
            start_new_session=True,
            bufsize=1024 * 1024,
        )
        if proc.stdin is not None:
            try:
                proc.stdin.write(full_prompt.encode("utf-8"))
                proc.stdin.close()
            except BrokenPipeError:
                pass

        _lifecycle.register(workdir, "iter", proc)
        deadline = time.monotonic() + _REFERENCE_PREPROCESS_TIMEOUT_S
        watchdog_stop = threading.Event()
        timed_out = threading.Event()

        def _watchdog() -> None:
            while not watchdog_stop.wait(timeout=2.0):
                if time.monotonic() > deadline:
                    print(
                        f"[claude-runner:{workdir.name}] reference-preprocess "
                        f"exceeded {_REFERENCE_PREPROCESS_TIMEOUT_S}s "
                        "wall-clock; terminating",
                        file=sys.stderr,
                    )
                    timed_out.set()
                    _lifecycle.terminate_slot(workdir, "iter")
                    return

        threading.Thread(
            target=_watchdog, daemon=True,
            name=f"claude-reference-preprocess-watchdog:{workdir.name}",
        ).start()

        try:
            for raw_line in iter(proc.stdout.readline, b""):
                try:
                    debug_stdout_fp.write(raw_line)
                except Exception:
                    pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _lifecycle.terminate_slot(workdir, "iter")
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                return False
            if timed_out.is_set() or proc.returncode != 0:
                return False
            return all(
                p.exists() and p.stat().st_size > 0
                for p in (clean, check, report)
            )
        finally:
            watchdog_stop.set()
            try:
                log_fp.close()
            except Exception:
                pass
            try:
                debug_stdout_fp.close()
            except Exception:
                pass
            _lifecycle.deregister(workdir, "iter")

    def _spawn_orchestrator(self, workdir: Path, prompt: str,
                            max_iters: int, auto: bool) -> None:
        """Invoke the installed ``$figmirror`` skill for the Stage-1 loop.

        Persists the session-id via --session-id so a future cancel
        + relaunch can correlate, and so the iter slot's session is
        addressable for diagnostics.
        """
        user_request = prompt.strip() or (
            "Mirror the visual style of the reference figure onto my data."
        )
        loop_policy = _format_loop_policy(max_iters=max_iters, auto=auto)
        full_prompt = _STEP1_PROMPT_TEMPLATE.format(
            workdir=workdir,
            skill_dir=_claude_skill_dir_for_prompt(),
            agents_dir=_claude_agents_dir_for_prompt(),
            max_iters=max_iters,
            loop_policy=loop_policy,
            user_request=user_request,
        )

        session_id = str(uuid.uuid4())
        # Prompt is piped via stdin (not positional) to avoid
        # `--add-dir <directories...>`'s variadic greedy parser
        # gobbling the prompt as if it were another directory. Also
        # avoids ARG_MAX issues if the prompt grows past the shell's
        # limit. Claude's --print mode accepts stdin natively.
        cmd = _uv_cmd(
            "claude", *_BASE_FLAGS,
            "--add-dir", str(workdir),
            "--session-id", session_id,
        )
        agent_log = workdir / "agent.log"
        log_fp = open(agent_log, "ab", buffering=0)
        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            env=_uv_env(),
            stdout=subprocess.PIPE,
            stderr=log_fp,
            stdin=subprocess.PIPE,
            start_new_session=True,
            bufsize=1024 * 1024,
        )
        try:
            proc.stdin.write(full_prompt.encode("utf-8"))
            proc.stdin.close()
        except Exception:
            pass
        _lifecycle.register(workdir, "iter", proc)
        # Record session_id BEFORE the reader runs so diagnostics
        # can correlate even if the run dies very early.
        with self._get_sessions_lock(workdir):
            sessions = read_sessions(workdir)
            sessions["iter"] = session_id
            write_sessions(workdir, sessions)
        threading.Thread(
            target=self._reader_iter,
            args=(workdir, proc, log_fp, max_iters),
            daemon=True,
            name=f"claude-iter:{workdir.name}",
        ).start()

    # ───── refine machinery ───────────────────────────────────────────

    def _refine_locked(self, workdir: Path, set_id: str, slot: str,
                       baseline_iters: list[int],
                       message: Optional[str],
                       adjustments: Optional[dict],
                       *, expected_n: Optional[int] = None) -> dict:
        # 1) Resolve prior session id for this set (turn 2+ path) or
        # mint a fresh one (turn 1).
        with self._get_sessions_lock(workdir):
            sessions = read_sessions(workdir)
            prior_sid = (sessions.get("refine") or {}).get(set_id)

        is_first_turn = prior_sid is None

        # 2) Compose the user-facing message. Mirror CodexRunner's
        # contract exactly: a single call to to_prose(adjustments,
        # message) handles all four (none / message-only / adj-only /
        # both) cases, and the empty-input case falls back to a
        # confirm-understanding sentinel rather than raising — the
        # frontend's "Send" button can fire with neither input today
        # and both other runners (mock + codex) accept that.
        user_message = to_prose(adjustments, message)
        if not user_message:
            user_message = "(no change requested — confirm understanding only)"

        # 3) Append the user turn to chat.jsonl up front.
        chat_log.append_turn(
            workdir,
            role="user",
            content=user_message,
            set_id=set_id,
            baseline_iters=sorted(set(baseline_iters)),
            adjustments=adjustments or None,
        )
        bus = event_bus.get_bus()
        bus.publish(workdir, slot, events.TurnStartEvent(data={
            "set_id": set_id,
            "baseline_iters": sorted(set(baseline_iters)),
        }))

        # 4) Expected refine_NNN index. Re-derive lazily if the caller
        # didn't pre-compute it (test/legacy paths) — production
        # ``refine()`` always passes ``expected_n`` so the marker and
        # the artifact filenames share a value.
        if expected_n is None:
            expected_n = _next_refine_index(workdir)

        # 5) Build the claude command. Prompt body is piped via stdin
        # (NOT positional) to avoid the variadic `--add-dir` flag
        # greedily eating it. See start()'s _spawn_orchestrator for
        # the same workaround.
        if is_first_turn:
            accumulated = _accumulated_rcparams_for_set(workdir, set_id)
            system_prompt = build_system_prompt(
                workdir,
                baseline_iters=baseline_iters,
                accumulated_rcparams=accumulated,
                user_message=user_message,
                expected_refine_index=expected_n,
            )
            session_id = str(uuid.uuid4())
            cmd = _uv_cmd(
                "claude", *_BASE_FLAGS,
                "--add-dir", str(workdir),
                "--session-id", session_id,
            )
            prompt_body = system_prompt
        else:
            session_id = prior_sid
            cmd = _uv_cmd(
                "claude", *_BASE_FLAGS,
                "--add-dir", str(workdir),
                "--resume", prior_sid,
            )
            prompt_body = user_message

        agent_log = workdir / f"agent_refine_{set_id}.log"
        log_fp = open(agent_log, "ab", buffering=0)
        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            env=_uv_env(),
            stdout=subprocess.PIPE,
            stderr=log_fp,
            stdin=subprocess.PIPE,
            start_new_session=True,
            bufsize=1024 * 1024,
        )
        try:
            proc.stdin.write(prompt_body.encode("utf-8"))
            proc.stdin.close()
        except Exception:
            pass
        _lifecycle.register(workdir, slot, proc)
        done_event = threading.Event()
        done_state = {"session_id": session_id, "exit_code": None}
        reader = threading.Thread(
            target=self._reader_refine,
            args=(workdir, slot, set_id, proc, log_fp, expected_n,
                  done_event, done_state),
            daemon=True,
            name=f"claude-refine:{workdir.name}:{set_id}",
        )
        reader.start()

        # 6) Wait for the promised artifact pair or process exit.
        # Claude can leave the JSON stream open after writing
        # refine_NNN.{png,json}; once both files parse, the user-visible
        # result is complete and can be published immediately.
        refine_output, reader_done = wait_for_refine_output_or_done(
            workdir,
            expected_n,
            done_event,
            timeout_s=REFINE_TURN_TIMEOUT_S,
        )
        if refine_output is None:
            png_path = workdir / f"refine_{expected_n:03d}.png"
            json_path = workdir / f"refine_{expected_n:03d}.json"
            if not reader_done:
                _lifecycle.terminate_slot(workdir, slot)
                reader.join(timeout=5.0)
                refine_output = salvage_refine_output_from_tmp(
                    workdir, expected_n,
                )
                if refine_output is not None:
                    reader_done = True
                else:
                    bus.publish(workdir, slot, events.TurnEndEvent(data={
                        "status": "failed",
                        "set_id": set_id,
                        "reason": "timeout_waiting_for_refine_artifacts",
                    }))
                    raise RefineFailed(
                        f"refine turn timed out after "
                        f"{REFINE_TURN_TIMEOUT_S}s without producing "
                        f"{png_path.name} + {json_path.name}"
                    )
            if refine_output is None:
                refine_output = salvage_refine_output_from_tmp(
                    workdir, expected_n,
                )
            if refine_output is None:
                exit_code = done_state.get("exit_code")
                bus.publish(workdir, slot, events.TurnEndEvent(data={
                    "status": "failed",
                    "set_id": set_id,
                    "reason": "missing_output_files",
                }))
                raise RefineFailed(
                    f"agent finished (exit={exit_code}) but did not produce "
                    f"{png_path.name} + {json_path.name}"
                )

        if not reader_done:
            _lifecycle.terminate_slot(workdir, slot, grace_seconds=1.0)
            reader.join(timeout=2.0)

        # 7) Record session id.
        if done_state.get("session_id"):
            with self._get_sessions_lock(workdir):
                sessions = read_sessions(workdir)
                sessions.setdefault("refine", {})[set_id] = (
                    done_state["session_id"]
                )
                write_sessions(workdir, sessions)

        # 8) Use the validated structured outcome.
        png_path = refine_output.png_path
        outcome = refine_output.outcome
        rcparams_delta = outcome.get("rcparams_delta") or {}
        review = outcome.get("review") or ""

        # 9) Publish + append assistant turn.
        ev_complete = events.RefineCompleteEvent(data={
            "set_id": set_id,
            "image_url": png_path.name,
            "rcparams_delta": rcparams_delta,
            "review": review,
            "refine_idx": expected_n,
        })
        # Bus.publish assigns + returns the monotonic seq for this
        # session key. RefineCompleteEvent is a plain @dataclass with
        # no .get method, so use the publish-returned seq (mirrors
        # CodexRunner._refine_locked).
        seq = bus.publish(workdir, slot, ev_complete)
        bus.publish(workdir, slot, events.TurnEndEvent(data={
            "status": "completed",
            "set_id": set_id,
        }))
        chat_log.append_turn(
            workdir,
            role="assistant",
            content=review,
            set_id=set_id,
            baseline_iters=sorted(set(baseline_iters)),
            image_url=png_path.name,
            rcparams_delta=rcparams_delta,
            review=review,
            refine_idx=expected_n,
            seq=seq,
        )

        return {
            # Server-side `_handle_refine` rewrites a relative
            # image_url into the absolute /static/<run>/<file> URL
            # (with proper urlquote of the run name). Returning the
            # bare filename here keeps that contract — see
            # interface.Runner.refine docstring + figcopy_serve.py
            # around the `image_url` rewrite block.
            "image_url": png_path.name,
            "rcparams_delta": rcparams_delta,
            "review": review,
            "set_id": set_id,
            "seq": seq,
        }

    # ───── reader threads ─────────────────────────────────────────────

    def _reader_iter(self, workdir: Path, proc: subprocess.Popen,
                     log_fp, max_iters: int) -> None:
        bus = event_bus.get_bus()
        slot = "iter"
        seen_iters: set[int] = set()
        seen_lock = threading.Lock()

        def _try_claim_iter(iter_n: int) -> bool:
            with seen_lock:
                if iter_n in seen_iters:
                    return False
                seen_iters.add(iter_n)
                return True

        # Filesystem poller — Claude's subagents write iter PNGs via
        # Write/Bash, NOT via something the stream-json event surface
        # lets us cleanly hook into. So poll the disk (same shape as
        # CodexRunner's poller).
        poll_stop = threading.Event()

        def _iter_file_poller() -> None:
            while not poll_stop.is_set():
                try:
                    for p in workdir.glob("img_iter*.png"):
                        iter_n = _parse_iter_n_from_path(p.name)
                        if iter_n is None:
                            continue
                        try:
                            if p.stat().st_size <= 0:
                                continue
                        except OSError:
                            continue
                        if not _try_claim_iter(iter_n):
                            continue
                        bus.publish(workdir, slot, events.IterCompleteEvent(data={
                            "iter": iter_n,
                            "img_url": p.name,
                            "pdf_url": None,
                        }))
                        with self._iter_state_lock:
                            if workdir in self._iter_state:
                                self._iter_state[workdir]["current_iter"] = iter_n
                        _write_status(workdir, state="running",
                                      current_iter=iter_n)
                except Exception:
                    pass
                poll_stop.wait(1.5)

        poll_thread = threading.Thread(
            target=_iter_file_poller, daemon=True,
            name=f"claude-iter-poll:{workdir.name}",
        )
        poll_thread.start()

        debug_stdout_fp = None
        try:
            debug_stdout_fp = open(workdir / "claude_stdout.jsonl",
                                   "ab", buffering=0)
        except Exception:
            debug_stdout_fp = None

        try:
            for raw in iter(proc.stdout.readline, b""):
                if debug_stdout_fp is not None:
                    try:
                        debug_stdout_fp.write(raw)
                    except Exception:
                        pass
                evt = _parse_jsonl_line(raw)
                if evt is None:
                    continue
                _publish_claude_event(bus, workdir, slot, evt)
            proc.wait()
        except Exception as e:
            bus.publish(workdir, slot, events.TurnEndEvent(data={
                "status": "failed",
                "reason": f"reader-thread crashed: {e}",
            }))
        finally:
            poll_stop.set()
            try:
                poll_thread.join(timeout=3.0)
            except Exception:
                pass
            try:
                log_fp.close()
            except Exception:
                pass
            if debug_stdout_fp is not None:
                try:
                    debug_stdout_fp.close()
                except Exception:
                    pass
            _lifecycle.deregister(workdir, slot)
            exit_code = proc.poll()
            protocol_failure = reviewer_protocol_failure_reason(workdir)
            if exit_code == 0:
                final = "shipped"
            elif exit_code is None or exit_code < 0:
                final = "cancelled"
            else:
                final = "failed"

            # Auto-finalize fallback: if claude exited non-zero but
            # we have at least one iter PNG, promote the latest
            # quality-floor-passing iter as figure.png so a "usable"
            # run isn't reported as failed. Use CodexRunner's
            # _pick_finalize_iter so both backends ship the SAME iter
            # for a given on-disk audit set — picking
            # disk_iters[-1] would silently ship an audit-failing
            # newer iter even when an earlier passing one exists.
            ship_path = workdir / "figure.png"
            disk_iters = sorted(
                n
                for p in workdir.glob("img_iter*.png")
                if (n := _parse_iter_n_from_path(p.name)) is not None
            )
            if (
                not protocol_failure
                and final != "shipped"
                and not ship_path.exists()
                and disk_iters
            ):
                chosen = _pick_finalize_iter(workdir, disk_iters)
                src = workdir / f"img_iter{chosen}.png"
                try:
                    data = src.read_bytes()
                    tmp = ship_path.with_name(ship_path.name + ".tmp")
                    tmp.write_bytes(data)
                    tmp.replace(ship_path)
                    sel = workdir / "selection.md"
                    if not sel.exists():
                        sel_tmp = sel.with_name(sel.name + ".tmp")
                        sel_tmp.write_text(
                            f"# Selection notes\n\n"
                            f"Selected: **iter {chosen}** "
                            f"(runner finalize after agent exited "
                            f"early; exit_code={exit_code}).\n\n"
                            f"_Auto-generated by ClaudeRunner._\n",
                            encoding="utf-8",
                        )
                        sel_tmp.replace(sel)
                    final = "shipped"
                except Exception as e:
                    print(
                        f"[claude-runner:{workdir.name}] auto-finalize "
                        f"failed: {e}",
                        file=sys.stderr,
                    )

            if protocol_failure and final != "cancelled":
                final = "failed"

            with self._iter_state_lock:
                if workdir in self._iter_state:
                    self._iter_state[workdir]["state"] = final
            current_iter = disk_iters[-1] if disk_iters else None
            _write_status(
                workdir,
                state=final,
                current_iter=current_iter,
                reason=protocol_failure,
            )
            bus.publish(workdir, slot, events.TurnEndEvent(data={
                "status": "completed" if final == "shipped" else final,
                **({"reason": protocol_failure} if protocol_failure else {}),
            }))

    def _reader_refine(self, workdir: Path, slot: str, set_id: str,
                       proc: subprocess.Popen, log_fp,
                       expected_n: int,
                       done_event: threading.Event,
                       done_state: dict) -> None:
        bus = event_bus.get_bus()
        debug_stdout_fp = None
        try:
            debug_stdout_fp = open(
                workdir / f"claude_stdout_{slot.replace(':', '_')}.jsonl",
                "ab", buffering=0,
            )
        except Exception:
            debug_stdout_fp = None

        captured_session_id = None
        try:
            for raw in iter(proc.stdout.readline, b""):
                if debug_stdout_fp is not None:
                    try:
                        debug_stdout_fp.write(raw)
                    except Exception:
                        pass
                evt = _parse_jsonl_line(raw)
                if evt is None:
                    continue
                if (captured_session_id is None
                        and evt.get("type") == "system"
                        and evt.get("subtype") == "init"):
                    sid = evt.get("session_id")
                    if isinstance(sid, str):
                        captured_session_id = sid
                _publish_claude_event(bus, workdir, slot, evt)
            proc.wait()
        except Exception as e:
            bus.publish(workdir, slot, events.TurnEndEvent(data={
                "status": "failed",
                "set_id": set_id,
                "reason": f"reader-thread crashed: {e}",
            }))
        finally:
            try:
                log_fp.close()
            except Exception:
                pass
            if debug_stdout_fp is not None:
                try:
                    debug_stdout_fp.close()
                except Exception:
                    pass
            _lifecycle.deregister(workdir, slot)
            if captured_session_id:
                done_state["session_id"] = captured_session_id
            done_state["exit_code"] = proc.poll()
            done_event.set()


# ─────────────────────── helpers (module-level) ─────────────────────────


def _parse_jsonl_line(raw: bytes) -> Optional[dict]:
    if not raw:
        return None
    try:
        text = raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return None
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _publish_claude_event(bus: event_bus.EventBus, workdir: Path,
                         slot: str, evt: dict) -> None:
    """Translate a claude stream-json event to our SessionEvent union.

    Claude's stream-json shapes (v2.1):

    - ``{"type":"system","subtype":"init","session_id":"<uuid>",...}``
      One per turn at the start.
    - ``{"type":"assistant","message":{"content":[...],...}}`` where
      content blocks are ``{type:"text",text:"..."}`` or
      ``{type:"tool_use",id:"toolu_...",name:"Bash",input:{...}}``.
    - ``{"type":"user","message":{"role":"user","content":[
      {"type":"tool_result","tool_use_id":"toolu_...","content":"..."},
      ...]}}`` — tool results from the previous tool_use blocks.
    - ``{"type":"result","subtype":"success","is_error":false,
      "result":"...","total_cost_usd":..., ...}`` at end of --print.
    """
    t = evt.get("type")

    if t == "system":
        # init message — no need to surface to the bus.
        return

    if t == "assistant":
        msg = evt.get("message") or {}
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                txt = block.get("text") or ""
                if txt.strip():
                    bus.publish(workdir, slot, events.TextEvent(data={
                        "text": txt,
                        "is_partial": False,
                    }))
            elif btype == "tool_use":
                bus.publish(workdir, slot, events.ToolCallStartEvent(data={
                    "call_id": block.get("id") or "",
                    "name": block.get("name") or "tool",
                    "args": _summarize_tool_input(block.get("input") or {}),
                }))
            # thinking / other blocks: skip (we don't surface model
            # reasoning in the SSE stream).
        return

    if t == "user":
        msg = evt.get("message") or {}
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    summary = " ".join(
                        c.get("text", "") for c in content
                        if isinstance(c, dict) and c.get("type") == "text"
                    )
                elif isinstance(content, str):
                    summary = content
                else:
                    summary = ""
                bus.publish(workdir, slot, events.ToolCallEndEvent(data={
                    "call_id": block.get("tool_use_id") or "",
                    "ok": not block.get("is_error", False),
                    "result": _truncate(summary, 800),
                    "error": summary if block.get("is_error") else None,
                }))
        return

    if t == "result":
        # Terminal `result` event in --print mode. The reader thread's
        # `finally` clause emits the proper TurnEndEvent based on
        # exit code, so we don't emit one here.
        return

    # Unknown event type — ignore (forward-compatible with newer
    # claude versions adding event variants).


def _summarize_tool_input(inp: dict) -> str:
    if "command" in inp:
        return _truncate(str(inp["command"]), 200)
    if "file_path" in inp:
        return f"file_path={inp['file_path']}"
    if "path" in inp:
        return f"path={inp['path']}"
    if "description" in inp:
        return _truncate(str(inp["description"]), 200)
    try:
        return _truncate(json.dumps(inp), 200)
    except Exception:
        return _truncate(str(inp), 200)


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _parse_iter_n_from_path(name: str) -> Optional[int]:
    if not (name.startswith("img_iter") and name.endswith(".png")):
        return None
    try:
        return int(name[len("img_iter"):-len(".png")])
    except ValueError:
        return None


def _next_refine_index(workdir: Path) -> int:
    return next_refine_index(workdir)


def _accumulated_rcparams_for_set(workdir: Path, set_id: str) -> dict:
    acc: dict = {}
    for entry in chat_log.read_turns(workdir, set_id=set_id):
        if entry.get("role") != "assistant":
            continue
        delta = entry.get("rcparams_delta") or {}
        if isinstance(delta, dict):
            acc.update(delta)
    return acc


def _write_status(workdir: Path, *, state: str,
                  current_iter: Optional[int],
                  reason: Optional[str] = None) -> None:
    data: dict = {"state": state}
    if current_iter is not None:
        data["current_iter"] = current_iter
    if reason:
        data["reason"] = reason
    try:
        atomic_write_text(
            workdir / "status.json",
            json.dumps(data, indent=2) + "\n",
        )
    except Exception as e:
        print(f"[claude-runner:{workdir.name}] status sidecar write failed: {e}",
              flush=True)


# ─────────────────────── Step-1 prompt template ─────────────────────────


def _format_loop_policy(*, max_iters: int, auto: bool) -> str:
    if auto:
        return (
            "Loop policy for this run:\n"
            "- Auto-until-shipped is enabled. Ignore the default iteration cap "
            "and keep running Drawer/Reviewer iterations until the Reviewer "
            "returns `ship`.\n"
            "- Continue through `close` and `off` verdicts when there is a "
            "clear next revision. Stop only for a true blocker, cancellation, "
            "or a protocol failure you cannot repair."
        )
    return (
        "Loop policy for this run:\n"
        f"- Maximum iterations: {max_iters}. Iterate N = 0..{max_iters - 1} "
        "unless the Reviewer returns `ship` earlier."
    )


def _load_reference_preprocessor_prompt() -> str:
    """Load the Stage-0 preprocessor prompt for the Claude backend.

    The on-disk file is a Claude subagent definition with YAML
    frontmatter (``name:`` / ``tools:`` / ``model:`` between two
    ``---`` fences). When piped through ``claude --print`` directly
    the model sees that frontmatter as instructions — the
    ``tools: Read, Write, Edit, Bash`` line in particular implies a
    restriction the top-level ``--permission-mode bypassPermissions``
    does NOT actually enforce, so leaving it in is misleading at best
    and a quiet contract divergence at worst (Adv #1, PR-27 round 1).
    Strip the frontmatter and return only the body, mirroring what the
    Codex sibling at ``.codex/skills/figmirror/references/preprocessor.md``
    already ships on disk.
    """
    for prompt_path in (
        _installed_claude_agents_dir() / "figure-preprocessor.md",
        _repo_claude_agents_dir() / "figure-preprocessor.md",
    ):
        if prompt_path.is_file():
            raw = prompt_path.read_text(encoding="utf-8")
            break
    else:
        raise FileNotFoundError(
            "FigMirror Claude preprocessor prompt not found in installed "
            "Claude agents or repo fallback"
        )
    # Frontmatter is exactly: leading "---\n", body, "---\n", body.
    # Anything else (no frontmatter, malformed, missing closing fence)
    # is returned unchanged so the caller still sees something sensible.
    if not raw.startswith("---\n"):
        return raw
    parts = raw.split("---\n", 2)
    if len(parts) < 3:
        return raw
    return parts[2].lstrip()


def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_HOME") or (Path.home() / ".claude")).expanduser()


def _installed_claude_skill_dir() -> Path:
    return _claude_home() / "skills" / "figmirror"


def _installed_claude_agents_dir() -> Path:
    return _claude_home() / "agents"


def _repo_claude_skill_dir() -> Path:
    return _REPO_ROOT / ".claude" / "skills" / "figmirror"


def _repo_claude_agents_dir() -> Path:
    return _REPO_ROOT / ".claude" / "agents"


def _claude_skill_dir_for_prompt() -> str:
    installed = _installed_claude_skill_dir()
    if (installed / "SKILL.md").is_file():
        return str(installed.resolve())
    return str(_repo_claude_skill_dir().resolve())


def _claude_agents_dir_for_prompt() -> str:
    installed = _installed_claude_agents_dir()
    if (installed / "figure-preprocessor.md").is_file():
        return str(installed.resolve())
    return str(_repo_claude_agents_dir().resolve())


_STEP1_PROMPT_TEMPLATE = """\
Use `$figmirror` for this task from this user-level Claude Code install:

    FIGMIRROR_SKILL_DIR = {skill_dir}
    FIGMIRROR_AGENTS_DIR = {agents_dir}

The skill is registered in your available-skills list; invoke it by following
that SKILL.md and the iter-loop spec it points to. If project-local skill or
agent files are also visible, use these user-level paths for this run.

Run the skill's Drawer/Reviewer loop against this workdir:

    WORKDIR = {workdir}

Inputs are already in the workdir:
- WORKDIR/inputs/reference_raw.png    (user's uploaded reference)
- WORKDIR/inputs/reference_clean.png  (Stage-0 cleaned crop)
- WORKDIR/inputs/data.txt             (user's data)

Stage-0 reference preprocessing has already run in this runner invocation.
Do not crop the reference again unless `inputs/reference_clean.png` is missing
or `inputs/reference_crop_report.md` says the crop failed.

{loop_policy}

Hard artifact limit for this run:
- Write every artifact atomically (.tmp + rename) so partial files
  are never observed by the watcher.

Produce iter files (figure_iter<N>.py, img_iter<N>.png,
notes_iter<N>.md, audit_iter<N>.json) into WORKDIR. When the
figure-critic verdict is `ship`, promote that iter's image to
WORKDIR/figure.png and write a selection.md describing the chosen iter.

Render every iter PNG at dpi=300.

User request (additional context):
{user_request}
"""
