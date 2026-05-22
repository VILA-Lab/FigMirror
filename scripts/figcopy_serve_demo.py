#!/usr/bin/env python3
"""Build a minimal mock figcopy workdir for testing figcopy_serve.py.

Creates synthetic iter artifacts (3 iters + final) so figcopy_serve renders
a non-empty page without needing to run the actual loop.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import rcParams

OUT = Path(__file__).parent.parent / "tools" / "figcopy_serve_demo_workdir"
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "inputs").mkdir(exist_ok=True)


def _draw(path: Path, jitter: float, palette: list[str], title: str) -> None:
    rcParams["font.family"] = "DejaVu Sans"
    fig, ax = plt.subplots(figsize=(5, 3.2))
    np.random.seed(0)
    x = np.arange(0, 51, 5)
    for i, col in enumerate(palette):
        y = 50 + 30 * (1 - np.exp(-x / 15)) + np.random.randn(len(x)) * jitter - i * 4
        ax.plot(x, y, marker="o", color=col, lw=1.5, ms=5,
                label=f"method {chr(65 + i)}")
    ax.set_xlabel("epoch")
    ax.set_ylabel("val accuracy (%)")
    ax.set_title(title)
    ax.legend(frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


# Reference image (the user's input)
_draw(OUT / "inputs" / "reference_clean.png", jitter=1.0,
       palette=["#1f77b4", "#ff7f0e", "#2ca02c"],
       title="Reference figure")
(OUT / "inputs" / "reference_raw.png").write_bytes(
    (OUT / "inputs" / "reference_clean.png").read_bytes()
)

# Dirty data paste
(OUT / "inputs" / "data.txt").write_text(
    "epoch | method | val_acc\n"
    "0 | resnet | 50.1\n"
    "5 | resnet | 62.3\n"
    "10 | resnet | 70.5\n"
    "...   (truncated for demo — original was 200 rows from a tensorboard paste)\n"
)

# Iter 0 — far from ref (default mpl, wrong palette)
_draw(OUT / "img_iter0.png", jitter=2.5,
       palette=["red", "green", "blue"],
       title="iter 0 attempt")
(OUT / "figure_iter0.py").write_text(
    "# Drawer iter 0: minimal first attempt\n"
    "import numpy as np, matplotlib.pyplot as plt\n"
    "fig, ax = plt.subplots()\n"
    "for c in ['red','green','blue']:\n"
    "    ax.plot(range(11), np.cumsum(np.random.randn(11)), color=c)\n"
    "ax.legend()\n"
    "fig.savefig('img_iter0.png')\n"
)
(OUT / "notes_iter0.md").write_text(
    "## iter 0 drawer notes\n"
    "- First pass; used default palette and default spines.\n"
    "- Did not consult L2 aesthetic-library yet.\n"
    "- Expecting reviewer to flag palette + spine treatment.\n"
)
(OUT / "audit_iter0.json").write_text(json.dumps({
    "fidelity": {"verdict": "off"},
    "quality_floor": {"passed": False, "violation_kinds": ["default-mpl-aesthetic", "spine-default"]},
    "anchor": {"what_is_right": [
        "[L1] x-axis label position is correct",
        "[L1] line plot kind matches reference",
    ]},
    "focus_themes": [
        "palette: red/green/blue is colorblind-hostile; sample reference's blue/orange/green",
        "spines: default 4-side mpl box; reference shows L+B only with hairline",
        "title style: reference uses small subtitle, not bold large",
    ]
}, indent=2))

# Iter 1 — closer (palette fixed)
_draw(OUT / "img_iter1.png", jitter=1.4,
       palette=["#1f77b4", "#ff7f0e", "#2ca02c"],
       title="iter 1 attempt")
(OUT / "figure_iter1.py").write_text(
    "# Drawer iter 1: palette corrected; spines L+B only\n"
    "# (full code elided in demo)\n"
)
(OUT / "notes_iter1.md").write_text(
    "## iter 1 drawer notes\n"
    "- Switched to Tableau-10 palette per reviewer's L2 reference.\n"
    "- Removed top/right spines.\n"
    "- Did not yet match the reference's tight inter-panel spacing (single panel here).\n"
)
(OUT / "audit_iter1.json").write_text(json.dumps({
    "fidelity": {"verdict": "close"},
    "quality_floor": {"passed": True},
    "anchor": {"what_is_right": [
        "[L1] palette now matches reference within ΔE < 5",
        "[L2] spines reduced to L+B only",
        "[L1] axis labels position",
        "[L1] line plot kind",
    ]},
    "focus_themes": [
        "tick density: reference uses 6 major ticks, we have 10",
        "marker size: reference markers slightly larger (~6pt vs our 5pt)",
    ]
}, indent=2))

# Iter 2 — final ship
_draw(OUT / "img_iter2.png", jitter=0.9,
       palette=["#1f77b4", "#ff7f0e", "#2ca02c"],
       title="iter 2 attempt")
(OUT / "figure_iter2.py").write_text(
    "# Drawer iter 2: tick density reduced; marker size matched\n"
    "# (full code elided in demo)\n"
)
(OUT / "notes_iter2.md").write_text(
    "## iter 2 drawer notes\n"
    "- Set MaxNLocator(6) on x-axis.\n"
    "- Marker size 6pt.\n"
    "- Expecting reviewer to ship.\n"
)
(OUT / "audit_iter2.json").write_text(json.dumps({
    "fidelity": {"verdict": "ship"},
    "quality_floor": {"passed": True},
    "anchor": {"what_is_right": [
        "[L1] palette",
        "[L2] spine treatment",
        "[L1] tick density",
        "[L1] marker size",
        "[L1] axis labels",
    ]},
    "focus_themes": []
}, indent=2))

# Final — copy of iter 2
import shutil
shutil.copy(OUT / "img_iter2.png", OUT / "figure.png")
(OUT / "selection.md").write_text(
    "# Selection notes\n\n"
    "Selected: **iter 2** (verdict=ship, floor=passed, all 5 anchor properties preserved).\n\n"
    "Rejected:\n"
    "- iter 0: floor failed (default-mpl-aesthetic + default spine).\n"
    "- iter 1: verdict=close; tick density and marker size still off from reference.\n"
)

print(f"demo workdir at {OUT}")
print(f"run: python3 scripts/figcopy_serve.py {OUT}")
