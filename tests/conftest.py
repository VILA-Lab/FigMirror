"""Pytest setup — make the `figcopy_runner` package importable.

The runtime code lives in `scripts/figcopy_runner/` so we add
`scripts/` to sys.path here. Avoids requiring a project install for
test runs (the project ships dependencies=[] and is meant to run
straight from the checkout).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
