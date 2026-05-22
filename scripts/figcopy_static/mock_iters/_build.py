#!/usr/bin/env python3
"""Build the figcopy_static/mock_iters/ tree.

The MockRunner copies these into a workspace run-dir at 4–8s
intervals to simulate the codex skill emitting iters. Each iter dir
contains generic filenames (img.png, code.py, notes.md, audit.json);
the runner renames into the iter-numbered convention on copy.

Source images come from the figcopy_serve_demo.py output that ships
with the project (at ``tools/figcopy_serve_demo_workdir/``). That demo
needs matplotlib to regenerate; this build script only **copies**
existing PNGs and writes JSON / py / md — so it has zero runtime deps
and is safe to run on the lightweight worktree.

The narrative across the 5 iters: iter0 is a default-mpl bad pass;
iters 1-3 progressively close on the reference but plateau; iter4
matches and ships. Visual progression: iter0 looks distinctly worse
(default red/green/blue); iters 1-3 share the cleaned-up palette image
(audit narrative carries the iter-by-iter refinement); iter4 is the
final ship-quality render.

Re-run this script after editing audit / notes / code content; it is
safe to re-run on top of an existing tree.

Usage:
    python3 scripts/figcopy_static/mock_iters/_build.py [--src DIR]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# When run directly, locate the source demo workdir in this checkout.
DEFAULT_SRC_CANDIDATES = [
    Path(__file__).resolve().parents[3] / "tools/figcopy_serve_demo_workdir",
]

OUT = Path(__file__).resolve().parent


def find_src(explicit: Path | None) -> Path:
    if explicit:
        if not explicit.is_dir():
            raise SystemExit(f"--src not a directory: {explicit}")
        return explicit
    for c in DEFAULT_SRC_CANDIDATES:
        if c.is_dir():
            return c
    raise SystemExit(
        "no source demo workdir found; pass --src "
        "<path-to-figcopy_serve_demo_workdir>"
    )


# Shared template strings — the iter-specific bits are filled in below.
NOTES_TEMPLATES = {
    0: (
        "## iter 0 drawer notes\n\n"
        "First-pass attempt. Used the default matplotlib palette and\n"
        "left the default 4-side spine box on. No L2 reference\n"
        "consultation yet; this is the shape codex emits before the\n"
        "reviewer round triggers refinement.\n\n"
        "Expecting reviewer to flag palette + spine treatment.\n"
    ),
    1: (
        "## iter 1 drawer notes\n\n"
        "Switched to Tableau-10 per reviewer's L2 reference. Removed\n"
        "top + right spines. Hairline weight on remaining spines.\n\n"
        "Did not yet match the reference's tick density — reviewer\n"
        "called this out as the highest-priority remaining issue.\n"
    ),
    2: (
        "## iter 2 drawer notes\n\n"
        "Set MaxNLocator(6) on the x-axis to bring tick density down\n"
        "from the default ~10. Marker size still slightly small\n"
        "compared to reference (~5pt vs ~6pt).\n"
    ),
    3: (
        "## iter 3 drawer notes\n\n"
        "Marker size bumped to 6pt. Line weight tightened to match\n"
        "reference more closely. Still seeing a minor gap on legend\n"
        "frame treatment.\n"
    ),
    4: (
        "## iter 4 drawer notes\n\n"
        "Removed legend frame; ms / mew now match reference within\n"
        "tolerance. Reviewer expected to ship.\n"
    ),
}

AUDIT_TEMPLATES = {
    0: {
        "fidelity": {"verdict": "off"},
        "quality_floor": {
            "passed": False,
            "violation_kinds": ["default-mpl-aesthetic", "spine-default"],
        },
        "anchor": {
            "what_is_right": [
                "[L1] x-axis label position",
                "[L1] line plot kind",
            ],
        },
        "focus_themes": [
            "palette: red/green/blue is colorblind-hostile; sample reference's blue/orange/green",
            "spines: default 4-side mpl box; reference shows L+B only with hairline",
            "title style: reference uses small subtitle, not bold large",
        ],
    },
    1: {
        "fidelity": {"verdict": "close"},
        "quality_floor": {"passed": True},
        "anchor": {
            "what_is_right": [
                "[L1] palette now matches reference within ΔE < 5",
                "[L2] spines reduced to L+B only",
                "[L1] axis labels position",
                "[L1] line plot kind",
            ],
        },
        "focus_themes": [
            "tick density: reference uses 6 major ticks, we have ~10",
            "marker size: reference markers slightly larger (~6pt vs our 5pt)",
        ],
    },
    2: {
        "fidelity": {"verdict": "close"},
        "quality_floor": {"passed": True},
        "anchor": {
            "what_is_right": [
                "[L1] palette",
                "[L2] spines L+B hairline",
                "[L1] tick density now 6 major",
                "[L1] axis labels",
            ],
        },
        "focus_themes": [
            "marker size: still ~5pt, reference is closer to 6pt",
            "line weight: 1.5pt; reference reads as 1.8pt",
        ],
    },
    3: {
        "fidelity": {"verdict": "close"},
        "quality_floor": {"passed": True},
        "anchor": {
            "what_is_right": [
                "[L1] palette",
                "[L2] spine treatment",
                "[L1] tick density",
                "[L1] line weight 1.8pt",
                "[L1] marker size 6pt",
            ],
        },
        "focus_themes": [
            "legend frame: reference omits the frame; we still draw a box around the legend",
        ],
    },
    4: {
        "fidelity": {"verdict": "ship"},
        "quality_floor": {"passed": True},
        "anchor": {
            "what_is_right": [
                "[L1] palette",
                "[L2] spine treatment",
                "[L1] tick density",
                "[L1] marker size",
                "[L1] line weight",
                "[L2] legend frameless",
            ],
        },
        "focus_themes": [],
    },
}

CODE_TEMPLATES = {
    0: (
        "# Drawer iter 0 — default-mpl pass.\n"
        "# (Mock content — real CodexRunner / ClaudeRunner will emit\n"
        "# the agent's actual generated script.)\n"
        "import numpy as np\n"
        "import matplotlib.pyplot as plt\n\n"
        "fig, ax = plt.subplots(figsize=(5, 3.2))\n"
        "x = np.arange(0, 51, 5)\n"
        "for i, c in enumerate(['red', 'green', 'blue']):\n"
        "    y = 50 + 30 * (1 - np.exp(-x / 15)) - i * 4\n"
        "    ax.plot(x, y, marker='o', color=c, label=f'method {chr(65+i)}')\n"
        "ax.set_xlabel('epoch'); ax.set_ylabel('val accuracy (%)')\n"
        "ax.legend()\n"
        "fig.savefig('img_iter0.png')\n"
    ),
    1: (
        "# Drawer iter 1 — palette switched to Tableau-10; spines L+B only.\n"
        "import numpy as np\n"
        "import matplotlib.pyplot as plt\n\n"
        "fig, ax = plt.subplots(figsize=(5, 3.2))\n"
        "palette = ['#1f77b4', '#ff7f0e', '#2ca02c']\n"
        "x = np.arange(0, 51, 5)\n"
        "for i, c in enumerate(palette):\n"
        "    y = 50 + 30 * (1 - np.exp(-x / 15)) - i * 4\n"
        "    ax.plot(x, y, marker='o', color=c, ms=5, lw=1.5,\n"
        "            label=f'method {chr(65+i)}')\n"
        "ax.set_xlabel('epoch'); ax.set_ylabel('val accuracy (%)')\n"
        "ax.spines['top'].set_visible(False)\n"
        "ax.spines['right'].set_visible(False)\n"
        "ax.legend(frameon=False)\n"
        "fig.savefig('img_iter1.png')\n"
    ),
    2: (
        "# Drawer iter 2 — tick density via MaxNLocator(6).\n"
        "import numpy as np\n"
        "import matplotlib.pyplot as plt\n"
        "from matplotlib.ticker import MaxNLocator\n\n"
        "# (palette + spines as iter 1; tick locator added)\n"
        "ax.xaxis.set_major_locator(MaxNLocator(6))\n"
    ),
    3: (
        "# Drawer iter 3 — line weight 1.8pt, marker size 6pt.\n"
        "# (delta vs iter 2: lw=1.8, ms=6)\n"
    ),
    4: (
        "# Drawer iter 4 — legend frameless; final tweaks.\n"
        "# (delta vs iter 3: ax.legend(frameon=False, edgecolor='none'))\n"
    ),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=None,
                    help="path to figcopy_serve_demo_workdir/ (auto-detect if omitted)")
    args = ap.parse_args(argv)

    src = find_src(args.src)
    print(f"source: {src}")
    print(f"output: {OUT}")

    # Source images we have: img_iter0.png, img_iter1.png, img_iter2.png.
    # Map onto the 5-iter narrative:
    #   iter0 ← demo iter0 (default-mpl bad pass)
    #   iter1 ← demo iter1 (palette fixed)
    #   iter2 ← demo iter1 (same image; audit progresses)
    #   iter3 ← demo iter1 (same; audit progresses further)
    #   iter4 ← demo iter2 (final ship)
    image_source = {
        0: src / "img_iter0.png",
        1: src / "img_iter1.png",
        2: src / "img_iter1.png",
        3: src / "img_iter1.png",
        4: src / "img_iter2.png",
    }

    for n in range(5):
        iter_dir = OUT / f"iter{n}"
        iter_dir.mkdir(parents=True, exist_ok=True)

        img_src = image_source[n]
        if not img_src.exists():
            print(f"  [iter{n}] WARN: missing source image {img_src}",
                  file=sys.stderr)
        else:
            shutil.copyfile(img_src, iter_dir / "img.png")

        (iter_dir / "code.py").write_text(CODE_TEMPLATES[n], encoding="utf-8")
        (iter_dir / "notes.md").write_text(NOTES_TEMPLATES[n], encoding="utf-8")
        (iter_dir / "audit.json").write_text(
            json.dumps(AUDIT_TEMPLATES[n], indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"  iter{n}: img + code + notes + audit")

    # Write a README so the directory's purpose is self-explanatory.
    readme = OUT / "README.md"
    readme.write_text(
        "# mock_iters/\n\n"
        "Pre-staged iter directories consumed by\n"
        "`scripts/figcopy_runner/mock.py` (`MockRunner`).\n\n"
        "Each `iter<N>/` contains generic-named files:\n\n"
        "    img.png       — the iter's render\n"
        "    code.py       — drawer's matplotlib script (mock)\n"
        "    notes.md      — drawer's iter notes (mock)\n"
        "    audit.json    — reviewer's verdict + anchors + focus themes\n\n"
        "On `runner.start(workdir)`, `MockRunner` copies each iter's\n"
        "files into the workdir at 4–8s intervals, renaming generic →\n"
        "iter-numbered to match the skill's filename convention\n"
        "(`img_iter<N>.png`, etc).\n\n"
        "Regenerate via:\n\n"
        "    python3 scripts/figcopy_static/mock_iters/_build.py\n",
        encoding="utf-8",
    )
    print(f"  README.md")
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
