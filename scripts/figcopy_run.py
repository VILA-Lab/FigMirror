#!/usr/bin/env python3
"""FigMirror run — one-command launcher for the FigMirror skill.

Prepares a workdir from your reference image + data file, then launches the
view-only `figcopy_serve.py` viewer in the background so you can watch iters
appear live as the Codex or Claude Code skill writes them.

Does not invoke the agent skill itself: you still drive FigMirror from a Codex
or Claude Code session by pointing the registered `figmirror` skill id at the
prepared workdir. This launcher's only job is to remove the manual "create dir,
copy inputs, start viewer" friction.

Usage:
    python3 scripts/figcopy_run.py \\
        --ref path/to/reference.png \\
        --data path/to/data.txt \\
        --workdir /tmp/myrun

    # then in a Codex or Claude Code session:
    #   /figmirror <workdir=/tmp/myrun>

Flags are passed through to figcopy_serve.py where applicable
(`--port`, `--no-open`, `--no-watch`).
"""
from __future__ import annotations

import argparse
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIEWER = HERE / "figcopy_serve.py"


def prepare_workdir(workdir: Path, ref: Path, data: Path) -> None:
    """Stage the reference + data into workdir/inputs/, matching SKILL.md layout."""
    inputs = workdir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)

    ref_raw_dst = inputs / "reference_raw.png"
    ref_dst = inputs / "reference_clean.png"
    data_dst = inputs / "data.txt"

    for dst in (ref_raw_dst, ref_dst):
        if dst.exists() and dst.samefile(ref):
            continue
        shutil.copy(ref, dst)
    if data_dst.exists() and data_dst.samefile(data):
        pass
    else:
        shutil.copy(data, data_dst)

    print(f"  workdir:    {workdir}")
    print(f"  reference → {ref_raw_dst}")
    print(f"  clean ref → {ref_dst}")
    print(f"  data      → {data_dst}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", type=Path, required=True,
                    help="reference figure image (PNG/JPG)")
    ap.add_argument("--data", type=Path, required=True,
                    help="user data file (CSV/TSV/markdown table/dirty text)")
    ap.add_argument("--workdir", type=Path, required=True,
                    help="path for iteration artifacts (created if missing)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="viewer host/interface (default: %(default)s)")
    ap.add_argument("--port", type=int, default=8765,
                    help="viewer port (default: %(default)s)")
    ap.add_argument("--no-open", action="store_true",
                    help="don't auto-open browser")
    ap.add_argument("--no-watch", action="store_true",
                    help="static viewer (no live refresh / lightbox)")
    args = ap.parse_args()

    if not args.ref.exists():
        print(f"error: --ref not found: {args.ref}", file=sys.stderr)
        return 1
    if not args.data.exists():
        print(f"error: --data not found: {args.data}", file=sys.stderr)
        return 1
    if not VIEWER.exists():
        print(f"error: viewer not found at {VIEWER}", file=sys.stderr)
        return 1

    workdir = args.workdir.resolve()
    print("→ preparing workdir")
    prepare_workdir(workdir, args.ref.resolve(), args.data.resolve())

    cmd = [sys.executable, str(VIEWER), str(workdir),
           "--host", args.host,
           "--port", str(args.port)]
    if args.no_open:
        cmd.append("--no-open")
    if args.no_watch:
        cmd.append("--no-watch")

    print("\n→ launching viewer")
    print(f"  {' '.join(cmd)}\n")
    proc = subprocess.Popen(cmd)

    # short delay so the server prints its banner before our hint
    time.sleep(0.6)

    print("─" * 60)
    print("Now drive FigMirror against the same workdir, e.g. in your")
    print("Codex or Claude Code session:")
    print()
    print(f"    /figmirror  workdir={workdir}")
    print()
    print(f"View the live trajectory at: http://{args.host}:{args.port}/figcopy_serve.html")
    print("Ctrl-C here to stop the viewer.")
    print("─" * 60)

    def _stop(signum, frame):
        proc.terminate()
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return proc.wait()


if __name__ == "__main__":
    sys.exit(main())
