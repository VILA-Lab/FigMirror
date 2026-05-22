"""figcopy_runner — backend-agnostic runners that drive the figcopy
loop and produce iter files into a workdir.

See ``interface.py`` for the abstract contract. Phase 2 ships:

- :class:`MockRunner` — synthesizes plausible iter files into a
  workdir on a timer; default backend during phase 2 dev. One mock
  serves both real backends because the contract they all honor
  (write iter files + status sidecar atomically) is identical.
- :class:`CodexRunner` — Phase 3 stub for the codex CLI backend.
- :class:`ClaudeRunner` — Phase 3 stub for the claude CLI backend.

Phase 3 fills in CodexRunner + ClaudeRunner; the swap is a one-line
change in ``figcopy_serve.run_workspace``.
"""

from __future__ import annotations

from .claude import ClaudeRunner
from .codex import CodexRunner
from .interface import Runner, TERMINAL_RUN_STATUSES, read_status_sidecar
from .mock import MockRunner

__all__ = [
    "Runner", "MockRunner", "CodexRunner", "ClaudeRunner",
    "read_status_sidecar", "TERMINAL_RUN_STATUSES",
]
