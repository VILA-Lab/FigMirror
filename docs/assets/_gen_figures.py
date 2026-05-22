"""Generate the three README figures via an OpenAI-compatible image API.

All three figures share the visual register of
`reference_claude_code_architecture.png` (the user-supplied L1 style anchor):
white background, rounded-rectangle nodes with thick pastel borders, hand-
drawn whiteboard feel, labeled black arrows, serif "Figure N …" caption
underneath.

Usage:
    python3 _gen_figures.py architecture
    python3 _gen_figures.py pipe
    python3 _gen_figures.py target
    python3 _gen_figures.py all          # serial; ~3 x ~160s
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
BASE_URL = os.environ.get("FIGMIRROR_IMAGE_API_BASE_URL", "https://api.openai.com/v1")
DEPLOYMENT = os.environ.get("FIGMIRROR_IMAGE_MODEL", "gpt-image-2")

# ============================================================================
# Shared style preamble — keep at top of every prompt for visual consistency
# ============================================================================
STYLE_PREAMBLE = """A clean academic paper-style figure on a pure white
background, drawn in the same hand-rendered whiteboard style as a NeurIPS or
arXiv paper Figure 1. Use rounded rectangles with thick (4-6 px) colored
borders and white fills, with a small monochrome line-art icon inside each
box next to a bold sans-serif label. Connect boxes with thick black arrows;
place small black sans-serif labels next to each arrow describing what flows
along it. No drop shadows, no gradients, no gridlines. Plenty of whitespace.
Sans-serif font for labels, serif font for the bottom caption. High
resolution. No watermarks."""


# ============================================================================
# Figure 1 — architecture (the 5-layer system structure)
# ============================================================================
PROMPT_ARCHITECTURE = STYLE_PREAMBLE + """

Layout (left to right, top to bottom):

1. Far left: a small black silhouette icon labeled "User" with a thin black
   border, with an outgoing arrow to the right labeled "dirty data +
   reference figure".

2. Next: a salmon/peach orange rounded rectangle labeled "Claude Code Skill"
   with a small terminal `</>` icon. Bidirectional arrows to the right node
   labeled "request" (going right) and "PDF + matplotlib script" (going
   left).

3. Center: a deep red-orange (terracotta) rounded rectangle labeled
   "Orchestrator" with a small loop / circular-arrows icon. This is the hub.

4. Top: a yellow rounded rectangle labeled "Drawer  (figure-illustrator)"
   with a small pencil / brush icon. Arrows go down to the Orchestrator
   labeled "render PNG + script" (going down) and "iter brief + anchor"
   (going up).

5. Right: a light blue rounded rectangle labeled "Reviewer  (figure-critic)"
   with a small magnifying-glass icon. Arrows go left to the Orchestrator
   labeled "JSON audit (ship / close / off)" (going left) and "draft + ref +
   prior audit" (going right).

6. Far right: a soft gray rounded rectangle labeled "Bounded PIL
   subprocess", with the small text "claude -p, Read+Bash only" underneath.
   Connected to the Reviewer with a thin two-way arrow.

7. Bottom-left: a green rounded rectangle labeled "Aesthetic Library  (L2)"
   with a small book / database icon, and small text "12 property sections,
   3 meta-principles" underneath. Two arrows go up: one to the Drawer
   labeled "conventions" and one to the Reviewer labeled "conventions".

8. Bottom-right: a soft purple rounded rectangle labeled "Anchor preserve
   list" with a small pin icon, and small text "what_is_right[], L1 / L2"
   underneath. A curved arrow loops back into the Orchestrator labeled
   "carry forward".

Below the diagram, on a clean line, a serif-font caption: "Figure 1  The
FigMirror loop. A drawer / reviewer pair, grounded by an aesthetic
library (L2) and an anchor preserve list, iterates dirty-data + reference
into a paper-style PDF. Stops on ship verdict or hard iter cap (6)."
"""


# ============================================================================
# Figure 2 — pipe (end-to-end pipeline, top-to-bottom)
# ============================================================================
PROMPT_PIPE = STYLE_PREAMBLE + """

This figure is a vertical pipeline diagram (top to bottom) showing the full
end-to-end flow of one FigMirror invocation, with four numbered
stages.

Top of figure: TWO small input boxes side by side, both with thin black
borders.
- Left input: a beige rounded rectangle labeled "dirty data" with a small
  terminal-paste icon, and small text underneath: "terminal paste, CSV, |
  delimited rows".
- Right input: a beige rounded rectangle labeled "reference figure" with a
  small picture-frame icon, and small text underneath: "paper screenshot,
  normal-reading size".
Both feed downward into stage [1] with a single converging arrow.

Stage [1] — a green rounded rectangle labeled "[1] Data echo / confirm",
with a small chat-bubble icon, and small text underneath: "agent normalizes
+ echoes parsed shape; user confirms before draw". Arrow downward to [2].

Stage [2] — the LOOP. A salmon/peach orange rectangular FRAME drawn with a
dashed thick border, labeled at top-left "[2] iter 0..N-1  (loop body)".
Inside the frame, three smaller rounded rectangles arranged horizontally:

- Left: yellow box "Drawer (figure-illustrator)" with a small pencil icon.
- Center: deep red-orange (terracotta) box "Orchestrator" with a small
  circular-arrows icon.
- Right: light blue box "Reviewer (figure-critic)" with a small
  magnifying-glass icon.

Connect them with thick black arrows that form a loop:
Drawer → Orchestrator label "draft.png + script", Orchestrator → Reviewer
label "audit task", Reviewer → Orchestrator label "JSON: anchor + floor +
verdict", Orchestrator → Drawer label "iter brief + anchor + themes". Add a
curved arrow at the bottom of the loop frame labeled "loop until ship".

Outside the frame on the left, attached by a thin arrow into the loop:
green rectangle "Aesthetic Library (L2)" with small text "3
meta-principles, 12 property sections".
Outside the frame on the right, attached by a thin arrow into the loop:
soft purple rectangle "Anchor preserve list" with small text
"what_is_right[]".

Arrow downward from [2] to [3].

Stage [3] — a yellow rounded rectangle labeled "[3] Stage 1 → Stage 2
transition" with a small gate / door icon, and small text underneath:
"agent asks; user explicit gate". Arrow downward to [4].

Stage [4] — TWO output boxes side by side at the bottom, both with thick
black borders.
- Left output: a soft cyan rounded rectangle labeled "figure_final.pdf"
  with a small page icon, and small text underneath: "Type-42 fonts,
  camera-ready".
- Right output: a soft cyan rounded rectangle labeled "figure_final.py"
  with a small python-snake icon, and small text underneath: "explicit DATA
  SECTOR; user edits inline".

Below the diagram, serif-font caption: "Figure 2  End-to-end pipeline.
Stages [1] data echo, [2] drawer / reviewer iter loop, [3] explicit Stage
1 → 2 user gate, [4] PDF + matplotlib outputs."
"""


# ============================================================================
# Figure 3 — target (the use case: who, in / out contract, P1-P5 principles)
# ============================================================================
PROMPT_TARGET = STYLE_PREAMBLE + """

This figure is a "before / after" use-case diagram showing target user and
input / output contracts, with the five product positioning principles
listed across the bottom.

Top half: two side-by-side panels separated by a large central black arrow
labeled "FigMirror" with a small loop icon next to the label.

LEFT panel ("BEFORE"):
- Soft beige rounded rectangle frame, labeled at top "BEFORE — what the user
  has".
- Inside: a small black silhouette of a researcher at a desk with a laptop
  (line art).
- To the right of the silhouette, two small stacked rectangles:
  - Top: pale yellow rectangle labeled "default-matplotlib plot" with a
    crude line-chart icon, small text "spines, default font, loose, off-
    register".
  - Bottom: pale yellow rectangle labeled "paper screenshot" with a small
    picture-frame icon, small text "the look they want".
- Caption underneath this panel: "ML researcher, mid-paper-writing".

RIGHT panel ("AFTER"):
- Soft cyan rounded rectangle frame, labeled at top "AFTER — what the user
  gets".
- Inside, two small stacked rectangles:
  - Top: cyan rectangle labeled "figure_final.pdf" with a small page icon,
    small text "Type-42 fonts, camera-ready".
  - Bottom: cyan rectangle labeled "figure_final.py" with a small
    python-snake icon, small text "DATA SECTOR — edit inline, no re-run".

Bottom strip: a horizontal row of FIVE small rounded rectangles, each with
its own thick colored border and a small icon, evenly spaced. Each box
contains a label "P1" through "P5" and a one-line principle:

- "P1  User contract on input quality"  (border: pale orange)  small icon:
  zoom-in
- "P2  The 80/20 envelope"               (border: pale yellow)  small icon:
  bell-curve
- "P3  Opinionated stylist, not faithful copyist" (border: pale red)
  small icon: artist palette
- "P4  Multi-figure consistency via serial chain" (border: pale green)
  small icon: 3 linked rectangles
- "P5  Stress envelope = mid-to-high paper figures" (border: pale blue)
  small icon: target / bullseye

Below the bottom strip, serif-font caption: "Figure 3  Target use case.
Input contract: paper screenshot at normal-reading size + dirty data.
Output contract: camera-ready PDF + a script with an editable DATA SECTOR.
Five product positioning principles bound the envelope (P1-P5)."
"""


PROMPTS = {
    "architecture": PROMPT_ARCHITECTURE,
    "pipe": PROMPT_PIPE,
    "target": PROMPT_TARGET,
}


def get_token() -> str:
    token = os.environ.get("FIGMIRROR_IMAGE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not token:
        raise SystemExit(
            "set FIGMIRROR_IMAGE_API_KEY or OPENAI_API_KEY before regenerating figures"
        )
    return token


def generate(name: str) -> int:
    if name not in PROMPTS:
        print(f"unknown figure '{name}'; choices: {list(PROMPTS)}", file=sys.stderr)
        return 2
    prompt = PROMPTS[name]
    out_path = HERE / f"{name}.png"
    body = {
        "model": DEPLOYMENT,
        "prompt": prompt,
        "size": "1536x1024",
        "n": 1,
        "output_format": "png",
        "quality": "high",
    }
    req = urllib.request.Request(
        f"{BASE_URL}/images/generations",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {get_token()}",
        },
        method="POST",
    )
    print(f"[{name}] POST  prompt_chars={len(prompt)}")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"[{name}] HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        return 2
    print(f"[{name}]   ok in {time.time() - t0:.1f}s")
    out_path.write_bytes(base64.b64decode(payload["data"][0]["b64_json"]))
    print(f"[{name}] wrote {out_path}  ({out_path.stat().st_size} bytes)")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    target = argv[1]
    names = list(PROMPTS) if target == "all" else [target]
    rc = 0
    for n in names:
        rc |= generate(n)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
