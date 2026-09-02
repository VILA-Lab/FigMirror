"""figcopy_runner — backend-agnostic runners that drive the figcopy
loop and produce iter files into a workdir.

See ``interface.py`` for the abstract contract. The package ships:

- :class:`MockRunner` — synthesizes plausible iter files into a
  workdir on a timer for offline UI development. One mock
  serves both real backends because the contract they all honor
  (write iter files + status sidecar atomically) is identical.
- :class:`CodexRunner` — launches the installed Codex FigMirror skill.
- :class:`ClaudeRunner` — launches the installed Claude Code FigMirror skill.

``figcopy_serve.run_workspace`` registers the real backends whose CLIs are
available on the current host and routes each run to its recorded backend.
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
