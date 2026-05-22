"""_lifecycle — shared subprocess registry + cancel implementation.

Per design.md §D9. Both CodexRunner and ClaudeRunner register every
live `subprocess.Popen` they spawn here, keyed by ``(workdir_str,
slot)``. ``terminate_slot`` is the single implementation of
"SIGTERM → 3 s → SIGKILL" used by both runners' ``cancel()``.

Slot conventions:

- ``"iter"`` — the Step-1 iter loop
- ``"refine:<set_id>"`` — a Step-2 refine turn on the given baseline set

Idempotency: ``terminate_slot`` on a slot with no registered subprocess
is a no-op (returns False without raising). Registry entries
deregister themselves automatically when the subprocess exits (the
reader thread does this in a ``finally`` clause).

Thread-safety: a module-level lock guards the registry. The lock is
held only during dict mutations, never across subprocess wait/sleep.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional


# ───── registry ──────────────────────────────────────────────────────

# (workdir_str, slot) → Popen
_LIVE: dict[tuple[str, str], subprocess.Popen] = {}
_LIVE_LOCK = threading.Lock()


def _key(workdir: Path | str, slot: str) -> tuple[str, str]:
    """Canonical (str, str) lookup key."""
    if isinstance(workdir, Path):
        return (str(workdir.resolve()), slot)
    return (str(Path(workdir).resolve()), slot)


def register(workdir: Path | str, slot: str,
             proc: subprocess.Popen) -> None:
    """Register a live subprocess under ``(workdir, slot)``.

    If a previous entry exists for the same key, it's overwritten
    (the previous subprocess SHOULD have deregistered itself; if it
    didn't, we'd be holding a stale Popen — overwriting is the safer
    of the two failure modes vs. silently rejecting the new run).
    """
    with _LIVE_LOCK:
        _LIVE[_key(workdir, slot)] = proc


def deregister(workdir: Path | str, slot: str) -> None:
    """Remove the registry entry. Idempotent."""
    with _LIVE_LOCK:
        _LIVE.pop(_key(workdir, slot), None)


def lookup(workdir: Path | str, slot: str) -> Optional[subprocess.Popen]:
    """Return the registered Popen for this slot, or None."""
    with _LIVE_LOCK:
        return _LIVE.get(_key(workdir, slot))


# ───── cancel ────────────────────────────────────────────────────────


def terminate_slot(workdir: Path | str, slot: str,
                   *, grace_seconds: float = 3.0) -> bool:
    """Cancel the subprocess under ``(workdir, slot)``.

    Sends SIGTERM, waits up to ``grace_seconds``, then SIGKILL if it's
    still alive. Deregisters from the registry on exit.

    Returns True if a subprocess was found and signaled; False if
    nothing was registered (idempotent no-op case).
    """
    proc = lookup(workdir, slot)
    if proc is None:
        return False

    # If the process already exited (race vs reader-thread deregister),
    # just clean up and return.
    if proc.poll() is not None:
        deregister(workdir, slot)
        return False

    # Step 1: SIGTERM. On POSIX this also reaches the whole process
    # group if the child was spawned with start_new_session=True (the
    # runners should do this; subprocess.Popen.terminate() only signals
    # the immediate child).
    try:
        proc.terminate()
    except ProcessLookupError:
        # Already dead in the brief window between poll() and
        # terminate().
        deregister(workdir, slot)
        return False
    except Exception:
        # Best-effort; fall through to wait/escalate.
        pass

    # Step 2: wait for grace period.
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            deregister(workdir, slot)
            return True
        time.sleep(0.05)

    # Step 3: SIGKILL escalation.
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    except Exception:
        pass

    # Give the OS a moment to reap; then deregister regardless.
    try:
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass
    deregister(workdir, slot)
    return True


# ───── helpers for runners ───────────────────────────────────────────


def popen_kwargs_for_managed_subprocess() -> dict:
    """Return the default Popen kwargs every managed subprocess should
    use.

    - ``start_new_session=True`` puts the child in its own process
      group; SIGTERM via ``proc.terminate()`` only reaches the child
      directly, but having a fresh session means we *can* signal the
      whole group via ``os.killpg`` if we ever need to.
    - ``stdout=subprocess.PIPE`` so the runner's reader thread can
      consume stream-json.
    - ``stderr=subprocess.PIPE`` for diagnostics into the agent log.
    - ``text=False`` because stream-json is line-oriented bytes; the
      reader thread does its own UTF-8 decode for control over
      handling partial UTF-8 sequences mid-token.
    """
    return {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": False,
        "start_new_session": True,
        # Larger stdout buffer so the kernel doesn't backpressure the
        # subprocess on bursty agent output.
        "bufsize": 1024 * 1024,
    }


def kill_group(proc: subprocess.Popen) -> None:
    """SIGKILL the entire process group of ``proc``.

    Useful when ``proc.terminate()`` reached the wrapper but the
    wrapper's children (e.g. matplotlib subprocesses the agent spawned)
    are the ones actually consuming CPU. Requires ``proc`` was started
    with ``start_new_session=True`` (see ``popen_kwargs_for_managed_subprocess``).
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
