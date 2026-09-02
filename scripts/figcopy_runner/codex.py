"""CodexRunner — real codex CLI backend (Phase 3).

Drives ``codex exec --json`` as a subprocess for both Step 1 (FigMirror iter
loop via the ``.codex/skills/figmirror/`` skill id) and Step 2
(multi-baseline multi-turn refine via an inline system prompt).

Stream-JSON event vocabulary (verified against codex-cli 0.130.0):

- ``{"type":"thread.started","thread_id":"<uuid>"}``           — first event; the
  ``thread_id`` is our durable session-id we persist in
  ``sessions.json`` for ``--resume``.
- ``{"type":"turn.started"}``                                  — turn boundary.
- ``{"type":"item.started","item":{...}}``                     — start of a non-
  message item (file_change, command_execution, ...). Mapped to
  :class:`events.ToolCallStartEvent`.
- ``{"type":"item.completed","item":{...}}``                   — completion.
  ``item.type == "agent_message"`` → :class:`events.TextEvent`;
  anything else → :class:`events.ToolCallEndEvent`.
- ``{"type":"turn.completed","usage":{...}}``                  — final event of
  a successful turn.

We normalize all of these into our SessionEvent union and publish to
the process-wide EventBus under key ``(workdir, "iter")`` or
``(workdir, "refine:" + set_id)``.

Step-1 invocation
-----------------

The runner invokes the installed ``$figmirror`` skill by name and runs
the subprocess from the run workdir. ``uv run --project <repo>`` still
supplies the repo's Python environment, but Codex skill dispatch should
exercise the user's installed skill package rather than a repo-local
``.codex/skills`` checkout.

Step-2 invocation
-----------------

Refine doesn't use a skill (per design.md §D4). On turn 1 we pass
the full inline system prompt (from
:func:`refine_prompt.build_system_prompt`) as the prompt body —
codex's session model treats the prompt body as the conversation's
first message, and the system prompt being in the transcript is
sufficient for subsequent ``--resume`` turns. On turn 2+ we only
send the new user message.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from . import _lifecycle, chat_log, event_bus, events
from .adjustments_to_prose import to_prose
from .interface import (
    atomic_write_text,
    clear_refine_in_flight,
    clear_refine_reservation,
    compute_set_id,
    ensure_reference_raw,
    is_data_placeholder,
    iteration_cap_failure_reason,
    mark_refine_in_flight,
    next_refine_index,
    read_sessions,
    read_status_sidecar,
    reserve_refine_index,
    reviewer_protocol_failure_reason,
    terminal_review_decision,
    write_sessions,
)
from .refine_completion import (
    salvage_refine_output_from_tmp,
    wait_for_refine_output_or_done,
)
from .refine_prompt import build_system_prompt


# Repo root — used for `uv run --project <repo>` and development fallback
# resources. The live Web UI path should route through the user's installed
# Codex skill package.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Default codex flags shared between start() and refine() — keep narrow.
# We DO NOT pin --profile: that's user-local config and would lock the
# runner to one user's `~/.codex/config.toml`. Use whatever profile the
# user's codex defaults to.
_BASE_FLAGS: list[str] = [
    "--json",
    "--skip-git-repo-check",  # workdirs are not git repos
]


def _uv_cmd(*args: str) -> list[str]:
    return ["uv", "run", "--project", str(_REPO_ROOT), *args]


def _uv_env() -> dict[str, str]:
    env = os.environ.copy()
    # Reason: default UV_CACHE_DIR to a per-repo path so first-use
    # `uv run` wheel pulls (matplotlib/numpy/pillow — hundreds of MB)
    # land in a predictable location instead of bloating $HOME/.cache
    # on shared/small-root hosts. Operators with their own preference
    # can export UV_CACHE_DIR themselves; setdefault preserves it.
    # `.artifacts/` is gitignored.
    env.setdefault("UV_CACHE_DIR", str(_REPO_ROOT / ".artifacts" / "uv-cache"))
    env["FIGMIRROR_PYTHON_CMD"] = shlex.join(_uv_cmd("python"))
    return env


# Refine turns are user-interactive and may spend time debugging render
# failures. By default, wait until the agent either writes the promised
# artifact pair or exits. Tests may monkeypatch a finite timeout to
# exercise failure handling.
REFINE_TURN_TIMEOUT_S: Optional[float] = None

# Data-gen pass timeout. The agent only has to inspect the reference
# image and write inputs/data.txt — should usually complete in ~30-90s.
# 5 min cap is generous for very large reference images / slow runs.
_DATAGEN_TIMEOUT_S = 300.0

# Reference preprocessing is another bounded one-shot agent pass. It
# trims screenshot/page margins and captions before the Drawer/Reviewer
# loop uses the image as L1.
_REFERENCE_PREPROCESS_TIMEOUT_S = 300.0


class RefineInFlight(RuntimeError):
    """Raised by refine() when a same-(workdir, set_id) turn is already
    in progress. Server translates to HTTP 409."""


class RefineFailed(RuntimeError):
    """Raised by refine() when the agent gave up / timed out without
    landing refine_NNN.{png,json}. Server translates to HTTP 500."""


class CodexRunner:
    """Real codex CLI backend. One instance per server process."""

    def __init__(self) -> None:
        # Per-(workdir, set_id) lock to prevent concurrent same-set refines.
        self._refine_locks: dict[tuple[str, str], threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # Per-workdir lock for sessions.json read-modify-write.
        # Two refine threads on DIFFERENT set_ids can complete near
        # simultaneously; both want to update sessions.json[refine][...].
        # Without this lock, B reads after A reads but before A writes
        # → B's write clobbers A's entry. Held only across the tight
        # read+mutate+write triple; never across subprocess waits.
        self._sessions_locks: dict[str, threading.Lock] = {}
        self._sessions_locks_guard = threading.Lock()
        # Step-1 in-memory state, mirroring MockRunner shape so the
        # server's _run_state path doesn't need a backend branch.
        self._iter_state: dict[Path, dict] = {}
        self._iter_state_lock = threading.Lock()

    def _get_sessions_lock(self, workdir: Path) -> threading.Lock:
        """Return (lazy-create) the per-workdir sessions.json mutex."""
        key = str(workdir)
        with self._sessions_locks_guard:
            lock = self._sessions_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._sessions_locks[key] = lock
            return lock

    # ───── public Runner Protocol ─────────────────────────────────────

    def start(self, workdir: Path, *, prompt: str = "",
              max_iters: int = 5, auto: bool = False) -> str:
        """Kick off a Stage-1 run.

        Returns immediately. The actual codex subprocess is launched
        from a background thread (``_stage1_orchestrate``) which:

        1. Runs a one-shot **reference-preprocess pass** first — a
           separate ``codex exec`` subprocess that preserves the raw
           upload and writes a clean ``inputs/reference_clean.png`` crop.
        2. If the user did not upload data, runs a one-shot **data-gen
           pass** first — a separate ``codex exec`` subprocess that
           reads the reference image and writes synthetic data to
           ``inputs/data.txt``. This replaces the previous behaviour
           where the Drawer agent invented data inline while plotting
           (which conflated data shape with figure style and made the
           per-iter Python scripts harder to read).
        3. Then invokes the installed ``$figmirror`` skill for the
           Drawer/Reviewer loop.

        Either path publishes events to the EventBus + writes status
        sidecars so the workpanel UI sees progress immediately, even
        while the data-gen pass is still running (which can be 30-90s).
        """
        workdir = workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)

        # Generation tracking: each call to start() increments a per-
        # workdir generation counter. Background-thread mutations to
        # `_iter_state` check their captured generation against the
        # current one before writing — so a stale `_stage1_orchestrate`
        # thread (e.g. after cancel + immediate restart) can't clobber
        # the next run's state. Concurrent start() for the same workdir
        # while a thread is already in-flight is rejected as a no-op so
        # we don't end up with two parallel orchestrators racing on the
        # same workdir (file-conflict + lifecycle-registry collision).
        with self._iter_state_lock:
            prior = self._iter_state.get(workdir, {})
            if prior.get("state") == "running":
                print(
                    f"[codex-runner:{workdir.name}] start() called while "
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
            name=f"codex-stage1:{workdir.name}",
        ).start()
        return workdir.name

    def _own_generation(self, workdir: Path, my_gen: int) -> bool:
        """Return True iff the in-memory run state still belongs to
        the calling thread's generation. A stale orchestrator thread
        that lost its workdir to a later start() will see False and
        bail without mutating state."""
        with self._iter_state_lock:
            cur = self._iter_state.get(workdir, {})
        return cur.get("gen") == my_gen

    def _stage1_orchestrate(self, workdir: Path, prompt: str,
                            max_iters: int, auto: bool,
                            my_gen: int) -> None:
        """Background driver: reference preprocess → optional data-gen → Drawer/Reviewer.

        Runs in its own thread so the HTTP POST that called ``start()``
        can return immediately. Publishes a paired TurnStart/TurnEnd
        envelope on the iter slot so SSE consumers see a complete
        lifecycle, including for Stage-0 failure paths that never reach
        the orchestrator subprocess.
        """
        bus = event_bus.get_bus()
        # Paired with TurnEndEvent on early-failure paths (reference
        # preprocessing failure, data-gen failure, no-reference-image).
        # The orchestrator subprocess's
        # reader thread emits its OWN TurnEnd on the happy path; we
        # don't emit one here unless we're going to short-circuit.
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

        # Stage 0a: normalize the uploaded reference screenshot into
        # a clean figure crop before any data generation or style
        # measurement happens.
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

        # Stage 0b: detect whether the inputs/data.txt placeholder is
        # still in place (i.e. the user did NOT upload data — the
        # server wrote DATA_PLACEHOLDER_TEXT at run-create time). If
        # so, run the data-gen pass first.
        data_path = workdir / "inputs" / "data.txt"
        needs_datagen = False
        try:
            if not data_path.exists():
                needs_datagen = True
            else:
                placeholder_text = data_path.read_text(
                    encoding="utf-8", errors="ignore",
                )
                if is_data_placeholder(placeholder_text):
                    needs_datagen = True
        except Exception:
            needs_datagen = True

        if needs_datagen:
            bus.publish(workdir, "iter", events.TextEvent(data={
                "text": (
                    "No data provided — generating synthetic data "
                    "from the reference image first…"
                ),
                "is_partial": False,
            }))
            ok = self._run_data_gen_pass(workdir)
            # Bail conditions (in priority order):
            # 1) cancel happened — leave the cancelled state in place
            # 2) a later start() superseded us — leave the new gen
            #    alone, do NOT mutate state
            # 3) data-gen failed — flip OUR run to failed and emit
            #    TurnEnd
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
                _write_status(workdir, state="failed", current_iter=None)
                bus.publish(workdir, "iter", events.TurnEndEvent(data={
                    "status": "failed",
                    "reason": "data generation pass failed",
                }))
                return
            bus.publish(workdir, "iter", events.TextEvent(data={
                "text": "Data ready. Starting Drawer/Reviewer loop.",
                "is_partial": False,
            }))

        # Last guard before spawning: if we lost ownership during the
        # data-gen pass (super rare — a cancelled+restarted flow), do
        # NOT spawn the orchestrator under a stale identity.
        if not self._own_generation(workdir, my_gen):
            bus.publish(workdir, "iter", events.TurnEndEvent(data={
                "status": "cancelled",
                "reason": "superseded by newer run",
            }))
            return
        self._spawn_orchestrator(workdir, prompt, max_iters, auto)

    def _run_reference_preprocess_pass(self, workdir: Path) -> bool:
        """Spawn a one-shot Codex agent to crop the reference image.

        The pass preserves the uploaded image as
        ``inputs/reference_raw.png`` and writes the cleaned L1 anchor to
        ``inputs/reference_clean.png``. It also asks the agent to write
        ``reference_crop_check.png`` and ``reference_crop_report.md`` so
        future debugging can compare before/after.
        """
        raw = ensure_reference_raw(workdir)
        clean = workdir / "inputs" / "reference_clean.png"
        check = workdir / "inputs" / "reference_crop_check.png"
        report = workdir / "inputs" / "reference_crop_report.md"
        if raw is None:
            print(
                f"[codex-runner:{workdir.name}] reference-preprocess: "
                "no reference image found",
                file=sys.stderr,
            )
            return False

        try:
            prompt = _load_reference_preprocessor_prompt()
        except Exception as exc:
            print(
                f"[codex-runner:{workdir.name}] reference-preprocess: "
                f"prompt load failed: {exc}",
                file=sys.stderr,
            )
            return False
        full_prompt = (
            "Use the FigMirror Stage-0 reference preprocessor for this task.\n"
            "If you inspect FigMirror SKILL.md or bundled references, use this "
            "user-level Codex skill install and resolve relative paths under it:\n\n"
            f"    FIGMIRROR_SKILL_DIR = {_codex_skill_dir_for_prompt()}\n\n"
            f"{prompt}\n\n---\n\n"
            f"Run Stage 0 reference preprocessing in this workdir:\n"
            f"{workdir}\n\n"
            "Use `inputs/reference_raw.png` as the raw upload. Write "
            "`inputs/reference_clean.png`, `inputs/reference_crop_check.png`, "
            "and `inputs/reference_crop_report.md`."
        )

        cmd = _uv_cmd(
            "codex", "exec", *_BASE_FLAGS,
            "--sandbox", "workspace-write",
            "-C", str(workdir),
            "-i", str(raw),
        )
        log_fp = open(workdir / "agent_reference_preprocess.log", "ab", buffering=0)
        debug_stdout_fp = open(
            workdir / "codex_stdout_reference_preprocess.jsonl",
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
                        f"[codex-runner:{workdir.name}] reference-preprocess "
                        f"exceeded {_REFERENCE_PREPROCESS_TIMEOUT_S}s "
                        "wall-clock; terminating",
                        file=sys.stderr,
                    )
                    timed_out.set()
                    _lifecycle.terminate_slot(workdir, "iter")
                    return

        threading.Thread(
            target=_watchdog, daemon=True,
            name=f"codex-reference-preprocess-watchdog:{workdir.name}",
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

    def _run_data_gen_pass(self, workdir: Path) -> bool:
        """Spawn a separate codex exec subprocess that writes
        ``inputs/data.txt`` from the reference image.

        Synchronous: blocks the calling (background) thread until the
        sub-codex exits OR the wall-clock watchdog fires. Returns True
        iff:

        - subprocess exited with code 0
        - ``inputs/data.txt`` is no longer the placeholder (exact
          match via ``is_data_placeholder``) AND is non-empty

        Uses the same codex profile as the orchestrator (whatever the
        user's ``~/.codex/config.toml`` defaults to). Attaches the
        reference image via ``-i`` so the data-gen agent can see what
        shape the data should take. Reasoning effort is whatever the
        chosen profile pins.

        The reviewer never sees this pass — only the Drawer reads
        ``inputs/data.txt``, and the Reviewer audit_view dirs include
        only images + the aesthetic library.

        Wall-clock bound: a separate watchdog thread fires
        ``_lifecycle.terminate_slot`` once ``_DATAGEN_TIMEOUT_S`` has
        elapsed since spawn. Without this, a codex agent that hangs
        with stdout open (network stall, infinite tool loop, etc.)
        would block the ``readline`` drain forever — ``proc.wait``'s
        own timeout only fires on the "stdout closed but proc not
        exited" path, which is not the common hang shape.
        """
        ref = workdir / "inputs" / "reference_clean.png"
        data_path = workdir / "inputs" / "data.txt"
        if not ref.exists():
            print(
                f"[codex-runner:{workdir.name}] data-gen: no reference "
                f"image at {ref}; skipping",
                file=sys.stderr,
            )
            return False

        cmd = _uv_cmd(
            "codex", "exec", *_BASE_FLAGS,
            "--sandbox", "workspace-write",
            "-C", str(workdir),
            "-i", str(ref),
        )
        log_fp = open(workdir / "agent_datagen.log", "ab", buffering=0)
        debug_stdout_fp = open(
            workdir / "codex_stdout_datagen.jsonl",
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
                proc.stdin.write(_DATAGEN_PROMPT.encode("utf-8"))
                proc.stdin.close()
            except BrokenPipeError:
                pass
        # Register under the "iter" slot so cancel() reaches this
        # subprocess. We deregister at end before the orchestrator
        # registers itself under the same slot.
        _lifecycle.register(workdir, "iter", proc)

        # Wall-clock watchdog. Daemon thread; fires terminate_slot
        # once the deadline elapses, which propagates to the proc via
        # the lifecycle registry. The drain loop below will then see
        # stdout EOF and exit normally; we report the timeout via the
        # `timed_out` flag set inside the watchdog.
        deadline = time.monotonic() + _DATAGEN_TIMEOUT_S
        watchdog_stop = threading.Event()
        timed_out = threading.Event()
        def _watchdog() -> None:
            while not watchdog_stop.wait(timeout=2.0):
                if time.monotonic() > deadline:
                    print(
                        f"[codex-runner:{workdir.name}] data-gen exceeded "
                        f"{_DATAGEN_TIMEOUT_S}s wall-clock; terminating",
                        file=sys.stderr,
                    )
                    timed_out.set()
                    _lifecycle.terminate_slot(workdir, "iter")
                    return
        threading.Thread(
            target=_watchdog, daemon=True,
            name=f"codex-datagen-watchdog:{workdir.name}",
        ).start()

        try:
            # Drain stdout into the debug file; we don't surface
            # individual events from the data-gen pass. The watchdog
            # above terminates the proc if it hangs, which closes
            # stdout and breaks us out of the loop.
            for raw in iter(proc.stdout.readline, b""):
                try:
                    debug_stdout_fp.write(raw)
                except Exception:
                    pass
            # Stdout closed; wait for proc to actually exit. Give it
            # a short grace period only — watchdog or normal close
            # both should have it exiting quickly here.
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Process is wedged with stdout closed but not exited;
                # force-kill via the registry and report failure.
                _lifecycle.terminate_slot(workdir, "iter")
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                return False
            if timed_out.is_set():
                return False
            if proc.returncode != 0:
                return False
            # Verify data.txt was actually written and is no longer
            # the placeholder. The placeholder-equality check is
            # strict (see is_data_placeholder) so an agent-generated
            # file that happens to mention the marker as a comment
            # is not misclassified.
            try:
                new_text = data_path.read_text(
                    encoding="utf-8", errors="ignore",
                )
            except Exception:
                return False
            if is_data_placeholder(new_text):
                return False
            if not new_text.strip():
                return False
            return True
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
        """Launch the Drawer/Reviewer orchestrator codex subprocess.

        Spawns ``codex exec`` with a prompt that explicitly names the
        installed ``$figmirror`` skill, registers the subprocess under
        the ``iter`` slot, and kicks off a daemon reader thread that
        parses stream-json events, publishes to the EventBus, and
        watches the workdir for new iter PNGs landing on disk.

        Fire-and-forget: returns once the subprocess is spawned and
        the reader thread is started. ``_reader_iter`` is responsible
        for the eventual lifecycle close (TurnEndEvent, status sidecar
        finalization, optional auto-finalize fallback).
        """
        # Build the prompt. The `$figmirror` mention is intentional:
        # it routes through Codex's native skill mechanism so this
        # backend exercises the installed skill package.
        user_request = prompt.strip() or (
            "Mirror the visual style of the reference figure onto my data."
        )
        loop_policy = _format_loop_policy(max_iters=max_iters, auto=auto)
        full_prompt = _STEP1_PROMPT_TEMPLATE.format(
            workdir=workdir,
            skill_dir=_codex_skill_dir_for_prompt(),
            max_iters=max_iters,
            loop_policy=loop_policy,
            user_request=user_request,
        )

        # codex exec invocation. Stage-1 must run the Drawer AND launch
        # a nested fresh-context Reviewer via `codex exec` (see the
        # skill's orchestrator-codex.md). The narrower workspace-write
        # sandbox blocks that nested CLI from reaching Codex's local
        # auth/proxy/app-server state, which caused the top-level agent
        # to write local audit JSON fallbacks and falsely ship bad runs.
        # Keep data-gen/refine narrower; only this nested-reviewer path
        # needs the user's normal full Codex environment.
        cmd = _uv_cmd(
            "codex", "exec", *_BASE_FLAGS,
            "--sandbox", "danger-full-access",
            "-C", str(workdir),
            full_prompt,
        )
        agent_log = workdir / "agent.log"

        # Open log file, spawn subprocess, register, start reader thread.
        log_fp = open(agent_log, "ab", buffering=0)
        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            env=_uv_env(),
            stdout=subprocess.PIPE,
            stderr=log_fp,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            bufsize=1024 * 1024,
        )
        _lifecycle.register(workdir, "iter", proc)
        threading.Thread(
            target=self._reader_iter,
            args=(workdir, proc, log_fp, max_iters),
            daemon=True,
            name=f"codex-iter:{workdir.name}",
        ).start()

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
        # Update bookkeeping for the iter slot. (Refine cancellations
        # surface as turn_end:cancelled events from the reader thread.)
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
                "CodexRunner.refine: baseline_iters must be non-empty"
            )
        workdir = workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        set_id = compute_set_id(baseline_iters)
        slot = f"refine:{set_id}"

        # Per-(workdir, set_id) lock. We check + acquire atomically so
        # two simultaneous POSTs for the same set don't both pass the
        # acquire() gate.
        lock = self._get_refine_lock(workdir, set_id)
        if not lock.acquire(blocking=False):
            raise RefineInFlight(
                f"refine on set_id={set_id} already in progress"
            )
        # PR #26 round-1 codex P2: initialize ``expected_n`` BEFORE the
        # try block so the ``finally`` cleanup never raises
        # ``UnboundLocalError`` when ``reserve_refine_index`` itself
        # fails (e.g. ENOSPC / EACCES while creating
        # ``.refine_reserved_NNN``). Mirrors ``ClaudeRunner.refine``.
        # Without this, an UnboundLocalError in the cleanup would mask
        # the original I/O failure class and make field debugging much
        # harder.
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

    # ───── refine machinery ───────────────────────────────────────────

    def _get_refine_lock(self, workdir: Path, set_id: str) -> threading.Lock:
        key = (str(workdir), set_id)
        with self._locks_guard:
            lock = self._refine_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._refine_locks[key] = lock
            return lock

    def _refine_locked(self, workdir: Path, set_id: str, slot: str,
                       baseline_iters: list[int],
                       message: Optional[str],
                       adjustments: Optional[dict],
                       *, expected_n: Optional[int] = None) -> dict:
        # 1) Translate the user's input into a single NL turn body.
        user_message = to_prose(adjustments, message)
        if not user_message:
            user_message = "(no change requested — confirm understanding only)"

        # 2) Decide turn-1 vs resume by consulting sessions.json.
        sessions = read_sessions(workdir)
        prior_sid = sessions.get("refine", {}).get(set_id)

        # 3) Append the user turn to chat.jsonl BEFORE invoking the
        # agent so SSE subscribers + page refreshes see the user's
        # message immediately (even on long agent latency).
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

        # 4) Determine the expected refine_NNN index so we can watch
        # for the file to land. Re-derive lazily if the caller didn't
        # pre-compute it (test/legacy paths) — production ``refine()``
        # always passes ``expected_n`` so the marker and the artifact
        # filenames share a value.
        if expected_n is None:
            expected_n = _next_refine_index(workdir)

        # 5) Build the codex exec invocation.
        if prior_sid is None:
            # Turn 1: full system prompt + user message in the body.
            accumulated = _accumulated_rcparams_for_set(workdir, set_id)
            system_prompt = build_system_prompt(
                workdir,
                baseline_iters=baseline_iters,
                accumulated_rcparams=accumulated,
                user_message=user_message,
                expected_refine_index=expected_n,
            )
            cmd = _uv_cmd(
                "codex", "exec", *_BASE_FLAGS,
                "--sandbox", "workspace-write",
                "-C", str(workdir),
                system_prompt,
            )
        else:
            # Turn 2+: resume the prior session with only the new user
            # message. System prompt lives in the persisted transcript.
            #
            # `codex exec resume` does NOT support -C / --sandbox CLI
            # flags (verified against codex-cli 0.130 --help). Without
            # those, codex defaults to the subprocess cwd for the
            # agent's shell tools and to the user's config sandbox
            # mode. We therefore:
            #   - launch with `cwd=str(workdir)` below (handled at the
            #     Popen site) so the agent's shell commands resolve
            #     paths inside the run dir
            #   - pin sandbox via `-c sandbox_mode='"workspace-write"'`
            #     so the agent can write refine_<N>.{py,png,json}
            #     atomically into the workdir
            #
            # Live test on 2026-05-12 showed turn-2 without these
            # losing workdir context: agent ran `git status` against
            # the upswing-overshot repo, then started editing
            # scripts/figcopy_runner/mock.py instead of producing
            # refine_002.png. Refine timed out + 500'd.
            cmd = _uv_cmd(
                "codex", "exec", "resume", *_BASE_FLAGS,
                "-c", 'sandbox_mode="workspace-write"',
                prior_sid,
                user_message,
            )

        agent_log = workdir / f"agent_refine_{set_id}.log"
        log_fp = open(agent_log, "ab", buffering=0)
        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            env=_uv_env(),
            stdout=subprocess.PIPE,
            stderr=log_fp,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            bufsize=1024 * 1024,
        )
        _lifecycle.register(workdir, slot, proc)
        # Reader thread translates events + watches for the .json file
        # landing. We block here on a threading.Event the reader sets.
        done_event = threading.Event()
        done_state = {"thread_id": None, "exit_code": None}
        reader = threading.Thread(
            target=self._reader_refine,
            args=(workdir, slot, set_id, proc, log_fp, expected_n,
                  done_event, done_state),
            daemon=True,
            name=f"codex-refine:{workdir.name}:{set_id}",
        )
        reader.start()

        # 6) Wait for the promised artifact pair or process exit.
        # The CLI can leave a live stream open after writing
        # refine_NNN.{png,json}; once both files parse, the UI result is
        # recoverable and should be treated as a successful turn.
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
                        f"refine on set_id={set_id} exceeded "
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

        thread_id = done_state.get("thread_id")

        # 7) Persist the captured thread_id (or update if changed).
        # RMW under the per-workdir sessions lock so a concurrent
        # refine on a DIFFERENT set_id can't clobber our update.
        if thread_id and prior_sid != thread_id:
            with self._get_sessions_lock(workdir):
                sessions = read_sessions(workdir)
                sessions["refine"][set_id] = thread_id
                write_sessions(workdir, sessions)

        # 8) Use the validated structured outcome.
        png_path = refine_output.png_path
        outcome = refine_output.outcome
        rcparams_delta = outcome.get("rcparams_delta") or {}
        review = outcome.get("review") or ""

        # 9) Publish refine_complete + append assistant turn to chat.
        ev_complete = events.RefineCompleteEvent(data={
            "set_id": set_id,
            "image_url": png_path.name,
            "rcparams_delta": rcparams_delta,
            "review": review,
            "refine_idx": expected_n,
        })
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
            "image_url": png_path.name,
            "rcparams_delta": rcparams_delta,
            "review": review,
            "set_id": set_id,
            "seq": seq,
        }

    # ───── reader threads ─────────────────────────────────────────────

    def _reader_iter(self, workdir: Path, proc: subprocess.Popen,
                     log_fp, max_iters: int) -> None:
        """Parse JSONL from codex stdout; publish events; watch for iter
        file completions to drive status.json's current_iter.

        Iter detection: the FigMirror skill drives matplotlib
        via shell `command_execution` (running `python
        figure_iter<N>.py`), NOT codex's `file_change` primitive — so
        the agent's iter PNGs DON'T arrive as `file_change` events.
        We complement the event-stream parse with a workdir-polling
        thread that watches for new ``img_iter<N>.png`` files and emits
        :class:`IterCompleteEvent` + updates the status sidecar.
        """
        bus = event_bus.get_bus()
        slot = "iter"
        # NOTE: `_stage1_orchestrate` already published the run's
        # TurnStartEvent on this slot before the data-gen pass (if any)
        # ran. Emitting another one here would duplicate the lifecycle
        # from the SSE consumer's point of view. The terminal
        # TurnEndEvent in our `finally` below is still the right pair
        # for it.

        # ── filesystem-poll thread: detect img_iter<N>.png arrivals ──
        # Daemon; exits when the parent reader thread does (we just
        # poll until proc.poll() returns).
        #
        # ``seen_iters`` is shared with the JSONL-reader path below
        # (which also detects file_change events). Both threads do
        # check-then-add against it; a `threading.Lock` makes that
        # atomic so we don't publish a duplicate IterCompleteEvent for
        # the same iter when codex emits a file_change at the same
        # moment the file lands on disk.
        seen_iters: set[int] = set()
        seen_lock = threading.Lock()
        def _try_claim_iter(iter_n: int) -> bool:
            """Atomically: return True if this thread is the first to
            see iter_n (caller should publish); False if already seen."""
            with seen_lock:
                if iter_n in seen_iters:
                    return False
                seen_iters.add(iter_n)
                return True

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
            name=f"codex-iter-poll:{workdir.name}",
        )
        poll_thread.start()

        # Tee codex stdout (JSONL events) to a debug file in the workdir.
        # Added 2026-05-12: when an iter run failed silently with only
        # stderr noise in agent.log, we had no way to see the actual
        # `turn.failed` event (which carries the API error message). The
        # tee runs at the line level — same buffering as the reader
        # loop, so partial lines aren't observable in the file either.
        debug_stdout_fp = None
        try:
            debug_stdout_fp = open(workdir / "codex_stdout.jsonl", "ab",
                                   buffering=0)
        except Exception:
            debug_stdout_fp = None  # best-effort; never fail the run

        thread_id = None
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
                published = _publish_codex_event(bus, workdir, slot, evt)
                if isinstance(published, tuple) and published[0] == "thread":
                    thread_id = published[1]
                    # RMW under the per-workdir sessions lock so a
                    # concurrent refine RMW can't clobber our update.
                    with self._get_sessions_lock(workdir):
                        sessions = read_sessions(workdir)
                        sessions["iter"] = thread_id
                        write_sessions(workdir, sessions)
                # `_publish_codex_event` returns ("iter_complete", N) when
                # it sees a `file_change` for an `img_iter<N>.png` but
                # does NOT publish the event itself — we do it here, so
                # the publish + state-update is gated by the same
                # `_try_claim_iter` lock as the filesystem-poll thread.
                # Belt-and-suspenders: codex emits file_change for files
                # written via its file-edit primitive, but the agent
                # usually runs matplotlib via shell, so the file-poller
                # is the primary detector; this path is the redundancy.
                if isinstance(published, tuple) and published[0] == "iter_complete":
                    iter_n = published[1]
                    if _try_claim_iter(iter_n):
                        bus.publish(workdir, slot, events.IterCompleteEvent(data={
                            "iter": iter_n,
                            "img_url": f"img_iter{iter_n}.png",
                            "pdf_url": None,
                        }))
                        with self._iter_state_lock:
                            if workdir in self._iter_state:
                                self._iter_state[workdir]["current_iter"] = iter_n
                        _write_status(workdir, state="running",
                                      current_iter=iter_n)
            proc.wait()
        except Exception as e:
            bus.publish(workdir, slot, events.TurnEndEvent(data={
                "status": "failed",
                "reason": f"reader-thread crashed: {e}",
            }))
        finally:
            poll_stop.set()
            # Join the file-poll thread BEFORE writing the terminal
            # status sidecar. If we didn't, the poller could observe a
            # late img_iter*.png arrival and write
            # status={state: "running", current_iter: N} *after* our
            # terminal write, flipping the run back to "running" and
            # confusing the trajectory page's terminal-state guard.
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
                # Killed by signal (cancel).
                final = "cancelled"
            else:
                final = "failed"
            # Terminal-action fallback: the deterministic gate may commit
            # `ship` or `stop_at_cap` immediately before the model process exits
            # without copying figure.png. Only that ledger-backed state may be
            # promoted here; provisional or invalid reviews remain failed.
            ship_path = workdir / "figure.png"
            disk_iters = sorted(
                n
                for p in workdir.glob("img_iter*.png")
                if (n := _parse_iter_n_from_path(p.name)) is not None
            )
            cap_failure = iteration_cap_failure_reason(disk_iters, max_iters)
            terminal_review = terminal_review_decision(workdir)
            failure_reason = protocol_failure or cap_failure
            cap_choice = None
            if (
                failure_reason is None
                and terminal_review is not None
                and terminal_review["action"] == "stop_at_cap"
            ):
                cap_choice = _pick_finalize_iter(workdir, disk_iters)
                if cap_choice is None:
                    failure_reason = (
                        "hard cap reached without a floor-passing close candidate"
                    )
            if (
                failure_reason is None
                and final != "cancelled"
                and terminal_review is None
            ):
                failure_reason = "orchestrator exited before a terminal review decision"
            if (
                not failure_reason
                and not ship_path.exists()
                and disk_iters
                and terminal_review is not None
            ):
                if terminal_review["action"] == "ship":
                    chosen = int(terminal_review["iter"])
                else:
                    chosen = int(cap_choice)
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
                            f"(runner finalize after terminal "
                            f"{terminal_review['action']} decision; agent exited "
                            f"early; exit_code={exit_code}).\n\n"
                            f"The deterministic review ledger authorized "
                            f"iter {chosen}; Codex exited before completing "
                            f"the final artifact copy.\n\n"
                            f"_Auto-generated by CodexRunner._\n",
                            encoding="utf-8",
                        )
                        sel_tmp.replace(sel)
                    final = "shipped"
                    if chosen not in seen_iters:
                        seen_iters.add(chosen)
                except Exception as e:
                    print(
                        f"[codex-runner:{workdir.name}] "
                        f"auto-finalize failed: {e}",
                        file=__import__('sys').stderr,
                    )

            if failure_reason and final != "cancelled":
                final = "failed"

            with self._iter_state_lock:
                if workdir in self._iter_state:
                    self._iter_state[workdir]["state"] = final
            # Preserve current_iter so the UI surfaces the last iter
            # the agent produced.
            last_iter = disk_iters[-1] if disk_iters else None
            _write_status(
                workdir,
                state=final,
                current_iter=last_iter,
                reason=failure_reason,
            )
            bus.publish(workdir, slot, events.TurnEndEvent(data={
                "status": "completed" if final == "shipped" else final,
                "exit_code": exit_code,
                **({"reason": failure_reason} if failure_reason else {}),
            }))

    def _reader_refine(self, workdir: Path, slot: str, set_id: str,
                       proc: subprocess.Popen, log_fp,
                       expected_n: int,
                       done_event: threading.Event,
                       done_state: dict) -> None:
        """Same as _reader_iter but for a refine turn. Captures the
        thread_id, normalizes events, waits for subprocess exit, then
        signals done_event."""
        bus = event_bus.get_bus()
        # Tee refine stdout to a per-slot debug file. See _reader_iter
        # for why we do this — same justification (visible turn.failed
        # events instead of silent codex exits).
        debug_stdout_fp = None
        try:
            debug_stdout_fp = open(
                workdir / f"codex_stdout_{slot.replace(':', '_')}.jsonl",
                "ab", buffering=0,
            )
        except Exception:
            debug_stdout_fp = None
        thread_id = None
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
                published = _publish_codex_event(bus, workdir, slot, evt)
                if isinstance(published, tuple) and published[0] == "thread":
                    thread_id = published[1]
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
            done_state["thread_id"] = thread_id
            done_state["exit_code"] = proc.poll()
            done_event.set()


# ─────────────────────── helpers (module-level) ─────────────────────────


def _parse_jsonl_line(raw: bytes) -> Optional[dict]:
    """Parse a single JSONL line, tolerating noise / empty lines."""
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


def _publish_codex_event(bus: event_bus.EventBus, workdir: Path,
                         slot: str, evt: dict):
    """Translate one codex JSONL event to our SessionEvent union and
    publish to the bus.

    Returns:
        - ("thread", thread_id) if the event was thread.started
        - ("iter_complete", iter_n) if the event was a file_change for
          an img_iter<N>.png (Step 1)
        - None otherwise
    """
    t = evt.get("type")

    if t == "thread.started":
        tid = evt.get("thread_id")
        return ("thread", tid) if tid else None

    if t == "turn.started":
        # We already publish TurnStartEvent at the runner level (with
        # richer context); skip duplicating.
        return None

    if t == "turn.completed":
        # The reader-thread's `finally` clause publishes the proper
        # TurnEndEvent based on exit code; skip emitting one here so
        # we don't double-end.
        return None

    if t == "item.started":
        item = evt.get("item") or {}
        if item.get("type") == "agent_message":
            return None  # we wait for item.completed for messages
        bus.publish(workdir, slot, events.ToolCallStartEvent(data={
            "call_id": item.get("id") or "",
            "name": item.get("type") or "tool",
            "args": _summarize_item(item),
        }))
        return None

    if t == "item.completed":
        item = evt.get("item") or {}
        item_type = item.get("type")
        if item_type == "agent_message":
            bus.publish(workdir, slot, events.TextEvent(data={
                "text": item.get("text", ""),
                "is_partial": False,
            }))
            return None
        # Tool call completion — figure out iter_complete on the side.
        bus.publish(workdir, slot, events.ToolCallEndEvent(data={
            "call_id": item.get("id") or "",
            "ok": item.get("status") == "completed",
            "result": _summarize_item(item),
            "error": None,
        }))
        # Detect Step-1 iter file landings. We DO NOT publish the
        # IterCompleteEvent here — the caller (_reader_iter) holds a
        # claim-and-publish lock that coordinates with the parallel
        # filesystem poller, so neither can race the other into a
        # duplicate. We just signal "this looks like iter N" via the
        # return tuple; the caller decides whether to publish.
        if slot == "iter" and item_type == "file_change":
            for change in item.get("changes") or []:
                path = change.get("path") or ""
                iter_n = _parse_iter_n_from_path(path)
                if iter_n is not None and change.get("kind") in {"add", "update"}:
                    return ("iter_complete", iter_n)
        return None

    # Unknown event type — log via stderr but don't crash.
    return None


def _summarize_item(item: dict) -> dict:
    """Compact a codex item dict into a small JSON-serializable summary
    for our ToolCall events (the full item can be large)."""
    return {
        "id": item.get("id"),
        "type": item.get("type"),
        "status": item.get("status"),
        "changes": item.get("changes"),
        "command": item.get("command"),
    }


_ITER_N_PATTERN = None  # lazy-compiled below

def _parse_iter_n_from_path(path: str) -> Optional[int]:
    """Extract <N> from an img_iter<N>.png path."""
    global _ITER_N_PATTERN
    if _ITER_N_PATTERN is None:
        import re
        _ITER_N_PATTERN = re.compile(r"img_iter(\d+)\.png$")
    m = _ITER_N_PATTERN.search(path)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _pick_finalize_iter(workdir: Path, disk_iters: list[int]) -> Optional[int]:
    """Pick the latest floor-passing `close` draft at the hard cap.

    A floor-failing or `off` draft is not an acceptable final artifact. Return
    ``None`` when the cap was reached without a candidate the public runner may
    safely promote.
    """
    import json
    for n in reversed(disk_iters):
        ap = workdir / f"audit_iter{n}.json"
        if not ap.exists():
            continue
        try:
            d = json.loads(ap.read_text())
            if (
                d.get("quality_floor", {}).get("passed") is True
                and d.get("fidelity", {}).get("verdict") == "close"
            ):
                return n
        except Exception:
            continue
    return None


def _next_refine_index(workdir: Path) -> int:
    """Mirror of refine_prompt._next_refine_index, here so codex.py
    doesn't import the prompt module just for this."""
    return next_refine_index(workdir)


def _accumulated_rcparams_for_set(workdir: Path, set_id: str) -> dict:
    """Reconstruct the accumulated rcparams snapshot for a `(workdir,
    set_id)` chat by replaying chat.jsonl assistant entries in order.

    This is the snapshot the system prompt embeds so the agent knows
    the current state."""
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
        print(f"[codex-runner:{workdir.name}] status sidecar write failed: {e}",
              flush=True)


# ─────────────────────── Step-1 prompt template ─────────────────────────


def _format_loop_policy(*, max_iters: int, auto: bool) -> str:
    if auto:
        return (
            "Loop policy for this run:\n"
            "- Automatic continuation is enabled: follow each deterministic "
            "review action until `ship` or the hard Drawer cap.\n"
            f"- Maximum Drawer iterations: {max_iters}. Never create iteration "
            f"{max_iters} or later."
        )
    return (
        "Loop policy for this run:\n"
        f"- Maximum Drawer iterations: {max_iters}. Iterate N = 0..{max_iters - 1} "
        "unless the Reviewer returns `ship` earlier."
    )


def _load_reference_preprocessor_prompt() -> str:
    for skill_dir in (_installed_codex_skill_dir(), _repo_codex_skill_dir()):
        prompt_path = skill_dir / "references" / "preprocessor.md"
        if prompt_path.is_file():
            return prompt_path.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "FigMirror Codex preprocessor prompt not found in installed "
        "Codex skill or repo fallback"
    )


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()


def _installed_codex_skill_dir() -> Path:
    return _codex_home() / "skills" / "figmirror"


def _repo_codex_skill_dir() -> Path:
    return _REPO_ROOT / ".codex" / "skills" / "figmirror"


def _codex_skill_dir_for_prompt() -> str:
    installed = _installed_codex_skill_dir()
    if (installed / "SKILL.md").is_file():
        return str(installed.resolve())
    return str(_repo_codex_skill_dir().resolve())


_STEP1_PROMPT_TEMPLATE = """\
Use `$figmirror` for this task from this user-level Codex skill install:

    FIGMIRROR_SKILL_DIR = {skill_dir}

The skill is registered in your `<skills_instructions>` available-skills list;
invoke it by following that SKILL.md workflow. If another `figmirror` entry is
visible from the project checkout, use FIGMIRROR_SKILL_DIR for this run and
resolve all bundled references relative to that directory.

Run the skill's Drawer/Reviewer loop against this workdir:

    WORKDIR = {workdir}

Inputs are already in the workdir:
- WORKDIR/inputs/reference_raw.png    (user's uploaded reference)
- WORKDIR/inputs/reference_clean.png  (Stage-0 cleaned crop)
- WORKDIR/inputs/data.txt             (user's data)

Stage-0 reference preprocessing has already run in this runner invocation.
Do not crop the reference again unless `inputs/reference_clean.png` is missing
or `inputs/reference_crop_report.md` says the crop failed.

Codex role policy for this run:
- You are the top-level Orchestrator. Follow the named-role dispatch and
  deterministic review state machine in `references/orchestrator-codex.md`.
- Dispatch drawing only to `figmirror-drawer` and visual review only to
  `figmirror-reviewer`; do not substitute generic roles.
- Do not launch `codex exec`, `codex`, `claude`, or another model process from
  inside the loop.

The runner has injected `FIGMIRROR_PYTHON_CMD`; use it for every Python command
as required by the skill.

{loop_policy}

Hard artifact limit for this run:
- Write every artifact atomically (.tmp + rename) so partial files are
  never observed by the watcher.

Produce iter files (figure_iter<N>.py, img_iter<N>.png, notes_iter<N>.md,
floor_selfcheck_iter<N>.txt, audit_iter<N>.json) into WORKDIR. When the
deterministic review gate returns `ship`, promote the selected iteration and
write the complete final artifact bundle required by SKILL.md.

Render every iter PNG at dpi=300.

User request (additional context):
{user_request}
"""


# ─────────────────────── Data-gen pass prompt ─────────────────────────


_DATAGEN_PROMPT = """\
You are generating synthetic data to back a FigMirror run.

The reference image is attached. Look at it and identify the data
shape it implies:

- How many series / categories / clusters are drawn?
- What are the x and y axes — units, range, scale (linear / log)?
- Roughly what trajectory does each series take (monotone increase,
  S-curve, oscillation, etc.)?

Generate plausible numeric data that matches that shape. Two
guardrails:

- Values follow the visible trend with realistic per-point variation.
  NOT a perfectly smooth curve — points should look measured, not
  fitted.
- NOT pure noise either. The trend must remain readable. Avoid
  variation so large that two adjacent points contradict the trend.

How many rows: scale to the figure type. Line plots typically want
10-50 points per series across the x range; bar / categorical plots
want one row per visible category; scatter plots want enough points
to fill the visible cluster (50-300 is fine).

Write the data to `inputs/data.txt` (relative to your cwd) in
whatever format is easiest for a matplotlib script to read — CSV,
TSV, JSON, Python literal, plain arrays, anything. Include a short
1-3 line header naming columns / series so the downstream drawer
script knows what each value means.

Write atomically: write to `inputs/data.txt.tmp` first, then rename
to `inputs/data.txt`. Do NOT modify any other file.

Output a one-sentence summary of what you wrote (rows × cols, format
chosen) and then stop. You do not need to plot anything.
"""
