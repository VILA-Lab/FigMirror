# Aesthetic Best-Practice Library (L2 Convention Layer)

> Read by both the Drawer and the Reviewer at the start of every run. Used as the
> SECONDARY anchor when the Stage-0 cleaned reference crop is the PRIMARY anchor.
>
> Purpose: catch cases where the reference image alone is insufficient — low-resolution
> screenshots, anti-aliasing artifacts on thin elements (spines/gridlines), unusual
> data shapes that need extension beyond the reference's series count, ambiguous
> typography under JPEG compression, etc.
>
> Designed to be **extended over time** as we add more reference figures, new data
> types, and new venue conventions. Each section is structured the same way:
>
> - **Most likely classes** — categorical menu (not a single value)
> - **Range** within each class
> - **Dependencies** — what other properties affect this one
> - **PIL reliability** — `✅ reliable` / `⚠️ partially reliable` / `❌ unreliable`
> - **Reference-vs-library precedence** — when L1 wins, when L2 takes over

---

## Compactness preference (meta-principle)

**Top-conference paper figures are tight, not airy.** Compact composition reads as
refined; loose composition reads as a notebook export. When in doubt about ANY
density-related property, **bias toward tight by default**.

This applies across the figure, not to one section:

- **Inter-panel spacing** (`wspace`, `hspace`) — see `Inter-panel spacing` section,
  default class is `tight`.
- **Legend internal spacing** (`columnspacing`, `handletextpad`, `borderpad`) — see
  `Legend treatment` section, default class is `tight`.
- **Tick padding** (text-to-tick gap) — `4–6 pt` in tight register, not the
  matplotlib default `4 pt` softened by extra layout space.
- **Title-to-axes padding** (`pad=` on `set_title`) — `4–6 pt` in tight register.
- **Outer margins** (`subplots_adjust left/right/top/bottom`) — only enough to
  fit axis labels + legend bands, no decorative whitespace.
- **Per-point label band** — labels packed close to their markers, not floating in
  a roomy band. Stack-line gap `1–2 pt`, not `4–6 pt`.
- **Marker-to-line ratio** — markers slightly larger than line width, not 2×+.

**When PIL or eye is genuinely ambiguous on which class a property belongs to, pick
tight.** The cost of one round of "you went too tight" is one revision; the cost of
"this looks like notebook output" is the figure being unshippable.

A specific anti-pattern to call out: **matplotlib's defaults are NOT in the tight
class** for any density property. `wspace=0.2`, `columnspacing=2.0`, `handletextpad=0.8`
all sit in the moderate class. Falling back to `plt.legend()` or `plt.subplots()`
without explicitly setting density params produces a moderate-class output —
camera-ready review will catch this.

---

## Hairline calibration: visible-but-recessive (meta-principle)

A second meta-principle, **distinct from compactness**: the figure has a class of
hairline elements (spines, gridlines, tick marks, light guides) whose role is to
**provide structure for the reader without competing with the data**. Each must be:

- **Visible enough** that the reader sees it when they look for it (axis values,
  panel boundaries, scale references). Too pale = element is functionally absent
  and the figure looks unstructured.
- **Recessive enough** that the reader does NOT notice it when reading the data.
  Too dark / too thick = element competes with data lines and the figure reads
  busy.

This is an **aesthetic balance**, not a single optimal value. The two failure
modes are symmetric:

| failure mode | symptom | example |
| --- | --- | --- |
| over-thin / over-pale | "I can't see anything" — element is invisible | gridlines at #f5f5f5 + 0.3pt + alpha 0.7 |
| over-thick / over-dark | element competes with data | gridlines at #888 + 1.5pt + alpha 1.0 |

Properties this applies to (each section will give specific class ranges; this
meta-principle says **stay in the visible-but-recessive band**):

- **Spines**: aim for clearly visible at print size; slightly thicker than gridlines.
- **Gridlines**: visibly grey against white background; thinner than spines.
- **Tick marks** (when present): same weight as spines, length minimal.
- **Light annotation guides** (e.g. zero-baseline rules): between gridlines and
  spines in weight.

Implementation hint: when extracting from a reference, **take the actual line
pixels, not the strip mean** (see `## Spines` and `## Gridlines` snippets).
Mean-of-strip is dominated by background and reports the line as too pale by 1–3
shades, which then renders too pale in the draft → "I can't see anything." This
is *both* a sampling bug *and* an aesthetic one: even with correct sampling, the
chosen value should land in the visible-but-recessive band, never at the extreme
pale end of the L2 class range.

SYMMETRIC anti-patterns — both extremes of an L2 range are wrong:

- **Pale-extreme bias** ("pick the lightest to be safe"): choosing `#ededed`
  for gridlines sits at the boundary of the class and can make gridlines
  rendered invisible in matplotlib output. **Don't pick the pale extreme.**
- **Dark-extreme bias** ("pick the darkest of mid-class to be visible"): the
  opposite over-correction is choosing `#d4d4d4`, the dark boundary of the
  mid-class, which can make gridlines compete with data lines. **Don't pick the
  dark extreme either.**

The right pick is the **literal middle of the class range** (e.g. `#e0e0e0` for
gridlines, NOT `#ededed` and NOT `#d4d4d4`). When the library says "mid-class,"
it means the arithmetic middle, not "darker end of mid" or "lighter end of mid."

There's also a matplotlib-rendering correction worth knowing: the same hex
renders visibly paler in matplotlib than in the reference's source rendering
(due to anti-aliasing on thin elements). So if the reference's measured
gridline is at the pale end of L2, **pick one notch darker than the
measurement** — NOT one notch darker than the entire class. The reference's
pale-extreme measurement is informative; the L2 class is just the safe band.

### Reviewer due diligence on hairline elements

For hairline elements such as sparse low-alpha gridlines, eyeball verification
is unreliable in both directions: the eye can miss a line that is present, or
infer one from defaults that the draft does not contain.

Observed dual failure: the Drawer disabled vertical gridlines with
`ax.xaxis.grid(False)` while claiming the evidence was clear, and the Reviewer
later affirmed both gridline directions by repeating library defaults instead
of verifying the draft. This created a wrong preserve item and prevented
convergence.

**The rule for hairline elements:**

1. Doer commits to a hairline-element class only with PIL-quoted evidence
   in notes (see `## Gridlines` direction property for the canonical example).
2. Reviewer affirms a hairline-element claim only after PIL-verifying it on
   the DRAFT image.
3. If reviewer cannot PIL-verify (e.g. the metric is genuinely hard), the
   reviewer says "I cannot confirm by eye" and skips the affirmation rather
   than fabricating one to fill the anchor list.
4. If source code disables a hairline element but the audit affirms its
   presence, the audit fails the floor because it is not verifying the rendered
   image. This is worse than missing the defect because a wrong preserve item
   can block the Drawer from re-enabling the element on the next iteration.

---

## Measurement humility & the perceive-iterate workflow (meta-principle)

This is the most important meta-principle in this library. A computed number's
confidence comes from the **heuristic that produced it**, not from its decimal
places. `per_panel_aspect = 1.92` is NOT more accurate than `"looks roughly
golden, around 1.5–1.7"` if the panel-bbox heuristic was brittle.

**False precision is a worse error mode than acknowledged uncertainty:**

- *Acknowledged uncertainty* ("eyeball says golden-ish") leaves room for the
  reviewer loop to converge.
- *False precision* ("PIL says 1.92") locks the doer to a wrong number, and
  the loop spends iters tuning around that wrong target.

### "PIL reliable" is conditional, not categorical

Most properties this library labels "PIL reliable" are reliable only **if the
heuristic is applied correctly to the right region of the image**. Brittle
points include:

- Panel bbox detection can pick up text frames, legend pills, annotations, or
  even the figure border instead of actual panel spines.
- Hairline width measurement is dominated by anti-aliasing halo, not the
  line itself.
- Inter-panel gutter detection depends on which y-band you scan; the wrong
  band gets contaminated by labels or data lines.
- Text height bbox depends on which glyph you measure (`g` descender vs
  cap-height of `M` vs digit height of `1`).
- Min-along-line for spine color is reliable only IF the strip is centered
  on the actual spine; off by 5 px and you're sampling background.

When the heuristic is non-trivial, **prefer eyeball + iterate over code +
lock**. Code's apparent confidence is unwarranted in these cases.

### The human workflow: constrain → perceive → render → adjust

For ANY property where the measurement heuristic is non-trivial:

1. **Constrain** with the L2 class menu — narrow to a band (e.g. "near golden
   ratio 1.4–1.7" for per-panel aspect; "tight class wspace 0.05–0.15"). The
   menu does the heavy lifting of "what range is plausible."
2. **Perceive** the reference against the menu — by eye, which class does it
   sit in? Pick a value in the middle of that class. **Trust the eye over a
   brittle measurement.** Document in notes as "L1-perceived" not "L1-PIL."
3. **Render** the figure with the chosen value.
4. **Perceive again** — does the rendered output read like the reference's
   class? Tighter? Looser?
5. **Adjust** on the next iter, within the class. The reviewer loop is the
   iteration mechanism; let it converge. Don't try to nail the exact value
   on iter 0.

This is how engineers actually make figures. The library's job is to provide
the L2 class menus AND to remind the doer not to substitute false-precision
code for the perceive-iterate cycle.

### When code measurement IS appropriate (the small list)

Code measurement is appropriate when:
- The arithmetic is trivial and unambiguous (`img.size[0] / img.size[1]` for
  full-image aspect).
- The sampling region is large and well-defined (palette of a clearly-bounded
  filled marker / line, not a thin spine).
- The result has been **eyeball-sanity-checked** before being treated as L1.

Code measurement is NOT appropriate as the SOLE source for:
- Per-panel aspect / per-panel bbox
- Hairline widths in points
- Sub-pixel anti-aliased element properties
- Font family / weight identification
- Visual gestalt properties (compactness, balance)

### How to record an eyeball-grounded anchor

When the doer commits to a value via eyeball + class menu (not via code),
record it in `notes_iter0.md` like this:

```
- Per-panel aspect: eyeballed reference panels, they look near golden
  (clearly not square, not wide-flat). L2 class "near golden ratio 1.4-1.7".
  Picking 1.55 in the middle of class. Will adjust on next iter if rendered
  output reads off-class.
```

NOT like this:

```
- Per-panel aspect: PIL panel-bbox detection gave 0.98.    ← WRONG
  Picking 0.98 to match reference.
```

The anchor entry the reviewer sees should be `[L1-perceived]` (perception-
grounded) rather than `[L1-PIL]` (measurement-grounded) when the underlying
measurement is brittle.

### Anti-pattern: code says X, doer locks to X without sanity check

A prior run made this mistake on per-panel aspect. Two plausible heuristics on
the same image returned conflicting values around 0.98 and 1.92; one was then
written as L1-PIL ground truth in anchor.what_is_right, and the loop tuned to
that wrong target. The right move would have been: eyeball reference's per-panel
aspect (clearly golden-ish), pick 1.5–1.7 from L2, render, perceive, adjust
within class.

This pattern likely lurks in other places too — the doer treating `[L1-PIL]
spine color = #1e1e1e` as ground truth when the strip may have been off-spine,
treating PIL gridline-direction detection as definitive when the band might
include partial data lines. **Always cross-check a code-measurement against
eyeball before locking it into anchor.**

---

## The L1/L2 hierarchy (read this before everything else)

- **L1 = the Stage-0 cleaned reference crop.** Highest authority. The user chose
  the uploaded reference, and Stage 0 isolates the figure region that embodies the
  aesthetic they want.
  - **L1-PIL** = code-measured (palette of large filled regions, full-image aspect).
    Use when the heuristic is trivial and unambiguous.
  - **L1-perceived** = eyeballed (font family, panel aspect class, density gestalt,
    spine count/sides by inspection plus line detection when possible). Use when
    code measurement is brittle. **A perceived L1 with acknowledged uncertainty
    beats a measured L1 with false precision** — see the Measurement humility
    meta-principle above.
- **L2 = this library** (paper-figure conventions). Used as fallback / sanity
  backstop / extension menu / class-bands within which to pick a value.
- **L3 = the model's own opinion about what looks good.** Disallowed. Reviewers and
  doers must ground every claim in either L1 (measured or perceived) or L2; purely
  opinion-based critique is unsupported L3 noise.

The hierarchy collapses for a property when L1 is unmeasurable on it. Concretely:

> **For a value estimate whose PIL reliability is `❌ unreliable`, L2 provides the
> fallback class vocabulary.** This applies to brittle values like spine color/width
> and font weight. It does NOT apply to visual-structure facts such as spine count,
> spine sides, gridline direction, tick presence, or panel topology; those remain L1
> claims and must be checked on the reference/draft directly.

For all other properties, **L1 wins** with **±10%** tolerance for measurable
quantities (aspect, sizes, ratios) and "same class" tolerance for categorical ones
(font family, marker shape, palette family).

---

## Spines (axis lines)

- **Most likely classes:**
  - Near-black hairline: `#000000`–`#444444`, width `0.5–1.0pt`
  - Soft mid-grey hairline: `#555555`–`#888888`, width `0.4–0.8pt`
  - (Very rarely seen, almost never correct: anything lighter than `#aaaaaa`. If your
    PIL sample says `#dcdcdc` for a spine, you sampled background, not the line.)
- **Sides visible:**
  - L+B only (NeurIPS / ICML / ICLR default — most common)
  - All 4 (Nature / Science default — paired with very thin weight)
- **Dependencies:** sometimes paired with tick treatment (no ticks → spines should
  not be too thin or panel reads as floating).
- **PIL reliability:** Color/width are ❌ **UNRELIABLE** with strip means. Spines
  are 1–2 px wide, so `mean()` of a strip is dominated by background pixels and
  comes out near-white. If you must measure color, use **min along the line
  direction** (per row → take the darkest column), then aggregate across rows. Or:
  detect line-vs-background pixels first, aggregate only over line pixels.
- **Count/sides are L1 visual-structure claims.** L2 says which classes are common;
  it does not decide whether the reference uses L+B or all-4. Count visible sides
  on the reference and on the draft before anchoring.
- **L1 vs L2:** use L1 for count/sides; use L2 as fallback class vocabulary for
  color/width. Never report a spine color lighter than `#888888` unless you have
  rigorous min-along-line evidence.

## Gridlines

- **Most likely classes (color/style):**
  - Solid mid-light grey: color **`#dadada`–`#e6e6e6`** (mid `#e0e0e0`),
    width `0.5–0.8pt`, alpha `0.8–1.0`
  - Dashed light grey: color `#cecece`–`#dcdcdc` (mid `#d6d6d6`), width
    `0.5–0.7pt`, alpha `0.9–1.0`
  - No gridlines (some Nature panels)
- **Calibration note:** the reference's
  measured gridline color is typically at the pale end (~`#ebebeb` is common).
  But matplotlib's anti-aliasing renders the SAME hex paler than the
  reference's source rendering, so a hex value matching the reference's
  measurement **renders visibly more recessive** in matplotlib output.
  Pick **one notch darker than the reference's measured value** to compensate.
  Example: ref measures `#ebebeb` → choose `#e0e0e0`–`#e3e3e3` for matplotlib.
- **SYMMETRIC anti-patterns** (both ends are wrong):
  - **Too pale** (`#e8e8e8`+): renders invisible in matplotlib at typical paper
    DPI. The user reads "no structure under the data."
  - **Too dark** (`#d4d4d4` or darker): visible but **competes with data
    lines**, especially in line plots where data lines are also greyish in
    one series. The user reads "busy/cluttered."
  - **Mid-class `#e0e0e0`** is the sweet spot for matplotlib output that
    pairs with most paper-figure references. Don't pick either extreme.
- **Direction property** (required for subtle gridline choices):
  - Classes: `horizontal-only` | `vertical-only` | `both` | `none`
  - **Default class is `both`.** A mixed grid is more common in modern paper
    figures than horizontal-only, despite matplotlib's common idiom of
    `ax.yaxis.grid(True)`.
  - **HARD RULE — PIL VERIFICATION REQUIRED TO DEVIATE FROM `both`.** Eyeball
    is NOT acceptable for direction. Gridlines are exactly the kind of
    sparse/recessive element that the human eye unreliably reads as "absent"
    when it's just light. Observed failure: the Drawer called
    `ax.xaxis.grid(False)` without quoted PIL evidence, then a later audit
    affirmed both gridline directions by repeating the default rather than
    checking the draft. The doer must:

    1. Run the PIL row+col profile snippet (below) on `reference_clean.png`.
    2. Quote the output (count of dark rows AND dark cols) verbatim in
       `notes_iter0.md` under `## Anchor measurements`.
    3. Only commit to `horizontal-only` / `vertical-only` if one direction
       has strictly zero darker-than-threshold rows/cols. If both have
       any non-zero count, the answer is `both`, regardless of how it
       "looks by eye."
    4. If the doer skips this verification, the implicit default is `both`,
       and the doer must NOT call `ax.xaxis.grid(False)` or
       `ax.yaxis.grid(False)`. Either run the snippet or render with both.

  - Implementation:
    - `both` → `ax.grid(True, which='major', axis='both', ...)` OR set both
      `ax.xaxis.grid(True, ...)` AND `ax.yaxis.grid(True, ...)` with the same
      style.
    - `horizontal-only` → `ax.yaxis.grid(True); ax.xaxis.grid(False)`
    - `vertical-only` → `ax.xaxis.grid(True); ax.yaxis.grid(False)`
    - **Never call `ax.xaxis.grid(False)` without PIL evidence quoted in
      notes.** "Evidence is clear" by eye is not evidence — gridlines are
      precisely the elements where the eye is most unreliable.

  - **Reviewer due diligence:** When the doer claims gridline
    direction in `anchor.what_is_right`, the reviewer MUST PIL-verify on the
    DRAFT image (same row+col profile snippet, applied to the draft):
    - If the audit affirms `BOTH directions` but PIL shows zero
      vertical-direction dark columns, flag a floor-level verification
      violation.
    - Do not repeat library defaults as draft facts; verify the exported image.
- **Dependencies:** `ax.set_axisbelow(True)` always — gridlines must sit behind data.
- **PIL reliability:**
  - Color: ⚠️ **CONDITIONALLY RELIABLE** — `mean()` is a trap (gives near-
    white because background dominates the strip). With **per-row/per-col
    darkest pixel + median** extraction (see snippet below, same technique as
    Spines), color IS measurable. Spine and gridline too-pale bugs both come
    from the same root cause: doer used mean-of-strip on a thin-line element.
  - Width: ❌ **UNRELIABLE** for exact pt value (effective width is anti-alias
    halo). L2 class is the floor.
  - Direction: ✅ **RELIABLE** via row-mean / col-mean profiling.
- **L1 vs L2:**
  - **Color: L1 wins IF properly sampled with per-line-darkest median.**
    L1 falls back to L2 if doer uses mean (don't).
  - **Width: L2 takes over.**
  - **Direction: L1 wins** (PIL-checkable). When in doubt, default class is `both`.

### Snippet — extracting gridline color the RIGHT way (per-col darkest, NOT mean)

```python
import numpy as np
from PIL import Image

ref = np.asarray(Image.open("reference_clean.png").convert("RGB"))
gray = ref.mean(axis=2)

# Step 1: locate a horizontal gridline row.
# Scan a panel-interior column band; find rows that are LOCAL MINIMA of mean
# brightness AND below ~250 (visibly darker than background).
panel_cols = slice(530, 780)        # adjust to a clean panel-interior range
panel_rows = slice(80, 280)         # avoid titles, label band, x-tick row

means = gray[panel_rows, panel_cols].mean(axis=1)
candidate_rows_in_slice = []
for r in range(2, len(means)-2):
    if means[r] < 250 and means[r] < means[r-1] and means[r] < means[r+1]:
        candidate_rows_in_slice.append((panel_rows.start + r, means[r]))
# Pick the row whose mean is in the LIGHT-ish range (not too dark — that
# would be a data line or text — not too light — that would be background).
# Gridlines typically have row_mean in [220, 245] for a 200-300 col strip.
gridlines = [r for r, m in candidate_rows_in_slice if 220 < m < 248]
print("detected horizontal gridlines at rows:", gridlines)

# Step 2: at one gridline row, sample per-col darkest pixel.
gline_y = gridlines[0]
strip = ref[gline_y-1:gline_y+2, panel_cols]   # 3 rows × n cols × RGB
g_strip = strip.mean(axis=2)                    # 3 × n
darkest_per_col = []
for c in range(g_strip.shape[1]):
    r_min = g_strip[:, c].argmin()
    if g_strip[r_min, c] < 250:                 # only count cols where line is visible
        darkest_per_col.append(strip[r_min, c])
color = np.median(darkest_per_col, axis=0).astype(int)
print(f"actual gridline color (per-col darkest median): "
      f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}")

# For VERTICAL gridlines, transpose: scan column means in row band, find local
# minima cols, then per-row darkest in a 3-col strip.
```

### Snippet — verifying gridline direction with PIL

```python
import numpy as np
from PIL import Image

ref = np.asarray(Image.open("reference_clean.png").convert("RGB"))
gray = ref.mean(axis=2)

# Pick a panel interior (avoid spines/labels/text) — adjust to your reference
panel = gray[100:260, 90:415]   # a 160×325 box inside one panel

row_means = panel.mean(axis=1)        # average brightness per row
col_means = panel.mean(axis=0)        # average brightness per col
H_lines = (row_means[10:-10] < 248).sum()    # rows visibly darker than bg
V_lines = (col_means[10:-10] < 248).sum()    # cols visibly darker than bg

if H_lines > 0 and V_lines > 0:
    direction = "both"
elif H_lines > 0:
    direction = "horizontal-only"
elif V_lines > 0:
    direction = "vertical-only"
else:
    direction = "none"
print(f"gridline direction: {direction}")
```

## Type (font family + size + weight)

### Identifying serif vs sans (eyeball heuristic)

This is a **commonly-missed identification** — ML papers come in BOTH families,
and matplotlib's default DejaVu Sans is wrong for ~half of them. Look at the
panel titles, axis labels, and tick numbers:

| Cue | Serif (Times / CMR / STIX) | Sans (Helvetica / Arial / DejaVu Sans) |
| --- | --- | --- |
| Stroke endings | Horizontal "feet" / serifs at stroke ends — visible on `I`, `M`, `T`, `0`, `8` | Clean cut endings, no flares |
| Stroke width | Variable: thick verticals, thinner horizontals | Uniform |
| Italic ε / math symbols | Curly mathit shape (calligraphic) | Simple slanted shape |
| Numerals | Slight bulges at top/bottom of `0`, `8`, `9` | Uniform geometric shapes |
| `M` apex | The interior `V` of `M` often touches baseline | Closed, doesn't reach baseline |
| Letter `R` leg | Curved or angled tail with serif | Straight diagonal, no tail |

**LaTeX-typeset ML papers default to serif** (Computer Modern Roman OR Times via
mathptmx — both common in NeurIPS / ICML / ICLR). **Word-typeset papers default
to sans** (Calibri / Arial). Industry blog reproductions often use sans. Don't
assume — look at the reference.

### Most likely classes (specific font names — pick one per family)

- **Times-style serif** (most common in modern LaTeX-typeset ML papers):
  - Times New Roman
  - Liberation Serif (open-source Times clone; ships with most Linux distros)
  - DejaVu Serif (matplotlib-bundled, always available)
  - Nimbus Roman No9 L
- **Computer Modern serif** (the "classic LaTeX look"):
  - Computer Modern Roman (cmr10) — needs LaTeX backend to render natively
  - Latin Modern Roman
  - STIX Two Text (matplotlib-friendly, mathtext-compatible)
- **Sans-serif** (Word-typeset papers, slide deck reproductions, some ML camera-ready):
  - Helvetica Neue / Helvetica
  - Arial / Arial Narrow
  - DejaVu Sans (matplotlib default — ALWAYS available, but generic)
  - Liberation Sans
- **Monospace** (in-figure code labels):
  - JetBrains Mono / Source Code Pro / Inconsolata

### Body / title sizes / weight

- Body: 7–11 pt at print
- Title: 9–13 pt (sometimes semibold for emphasis)
- Math labels: same size as body
- Weight: `regular` by default. Bold body is rare and almost always wrong.
- Math: italic for variables (`ε`, `σ`, `x`, `n`); upright for function names.

### matplotlib usage (the right way to commit to a family)

```python
import matplotlib.pyplot as plt

# === Times-style serif (most common for LaTeX-typeset reference) ===
plt.rcParams["font.family"] = "serif"
plt.rcParams["font.serif"] = [
    "Times New Roman", "Liberation Serif", "DejaVu Serif", "Nimbus Roman No9 L"
]
plt.rcParams["mathtext.fontset"] = "stix"   # math glyphs match serif body

# === Computer Modern style (classic LaTeX look) ===
plt.rcParams["font.family"] = "serif"
plt.rcParams["font.serif"] = [
    "Computer Modern Roman", "Latin Modern Roman", "STIX Two Text", "DejaVu Serif"
]
plt.rcParams["mathtext.fontset"] = "cm"

# === Sans-serif (Word-typeset / industry register) ===
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = [
    "Helvetica", "Arial", "DejaVu Sans", "Liberation Sans"
]
plt.rcParams["mathtext.fontset"] = "dejavusans"
```

Set rcParams **before** any plotting calls. The fallback chain matters: matplotlib
picks the first available font.

### PIL reliability

- Family (serif vs sans): ⚠️ identifiable by careful eyeball — see cue table above.
  PIL alone cannot infer family without OCR-style analysis. The doer must LOOK,
  not measure.
- Body size in pixels: ✅ reliable via text bbox height.
- Weight: ❌ unreliable from low-res screenshot — use L2 default.

### L1 vs L2

- Family: **L1 narrows by eye via the cue table; L2 picks the specific font
  within the identified family.**
- Default class when truly ambiguous (and the venue is unknown): **serif
  Times-style** (most common in ML LaTeX papers). DO NOT default to DejaVu
  Sans just because it's matplotlib's default.
- Size: L1 wins via PIL pixel-height measurement, ±15% tolerance.
- Weight: L2 wins; default `regular`.

## Inter-panel spacing (wspace / hspace / margins)

Matplotlib's `wspace` and `hspace` are expressed as fractions of average axis
width/height. **The right values for top-conference paper figures are MUCH
smaller than matplotlib defaults** — this is the single most common "AI slop"
giveaway in figure spacing: the figure looks like a notebook export, not a
paper figure.

- **Most likely classes:**
  - **Tight (camera-ready paper register — DEFAULT for top venues):**
    `wspace 0.05–0.15`, `hspace 0.15–0.30`
  - **Moderate (slide deck, workshop paper, exploratory notebook with cleanup):**
    `wspace 0.15–0.25`, `hspace 0.30–0.45`
  - **Generous (rare in papers; common in raw matplotlib output):**
    `wspace 0.25–0.40`, `hspace 0.40–0.60`
  - matplotlib's default is `wspace=0.2, hspace=0.2`, which sits in the
    **moderate** class, NOT the tight class. Falling back to defaults gives
    moderate spacing, which reads non-paper.
- **Dependencies:**
  - When per-point labels at panel edges (e.g. V2 value at the rightmost x)
    threaten cross-panel bleed, the right defense is `xlim` padding inside
    each panel + `ha='right'` on rightmost labels — NOT widening `wspace`.
    Widening wspace as the only defense produces a roominess problem. Observed
    failure: extra gutter hid the immediate collision but made the figure read
    loose instead of paper-tight.
  - When ticks / tick labels need room between panels, `sharex=True` +
    tick labels only on the left column lets you go even tighter (0.05–0.08).
  - Outer margins (`left`, `right`, `top`, `bottom` in `subplots_adjust`)
    should be just enough to accommodate axis labels + legend / title
    bands. Typical values: `left=0.06–0.09, right=0.98–0.99,
    top=0.82–0.88, bottom=0.10–0.13`.
- **PIL reliability:** ✅ **RELIABLE** for measuring `gap_px / panel_width_px`
  on the reference. Scan a horizontal band IN THE DATA AREA (not titles, not
  annotation bands, not tick rows) and count "white gutter" runs between
  "panel-content" runs.
- **L1 vs L2:** **L1 wins** with ±5% tolerance once measured. **Default class
  when L1 is ambiguous: tight (wspace ~0.08, hspace ~0.22).** Do NOT default
  to matplotlib's `0.2 / 0.2` — that's the moderate class, wrong register.

### Snippet — measure reference's wspace ratio from the image

```python
import numpy as np
from PIL import Image

ref = np.asarray(Image.open("reference_clean.png").convert("RGB"))
H, W, _ = ref.shape
gray = ref.mean(axis=2)

# Scan a band inside the data area of one row (avoid titles + per-point labels).
# Adjust y range to your reference; want 30-50px of clear data area.
band = gray[H//4 + 50 : H//4 + 100, :]

# A column is "in panel" if it has at least one dark pixel (gridline, data,
# spine, etc.) and "in gutter" if every pixel is near-white.
col_dark = band.min(axis=0) < 240

# Find contiguous runs
runs = []
i = 0
while i < len(col_dark):
    if col_dark[i]:
        j = i
        while j < len(col_dark) and col_dark[j]:
            j += 1
        if j - i > 30:  # filter to substantive panel runs
            runs.append((i, j-1, j-i))
        i = j
    else:
        i += 1

if len(runs) >= 3:
    panel_widths = [r[2] for r in runs[:3]]
    gaps = [runs[1][0] - runs[0][1] - 1, runs[2][0] - runs[1][1] - 1]
    wspace_estimate = np.mean(gaps) / np.mean(panel_widths)
    print(f"reference wspace estimate: {wspace_estimate:.3f}")
    # Pick a class: <0.15 → tight; 0.15-0.25 → moderate; >0.25 → generous
```

## Aspect ratios — figure-level vs per-panel-level (TWO distinct properties)

These are commonly conflated. This library splits them because a doer can match
the figure-level aspect perfectly but still produce
"扁扁的" (flat-wide) panels via wrong hspace.

### A. FIGURE aspect (W/H of the whole canvas)

- **Common reference points:** golden ratio `1.618`, 4:3 `1.333`, 16:9 `1.778`,
  "wide and short" `1.9–2.2`.
- **Typical figure aspects by grid:**
  - 1×1: `1.3–1.8`
  - 1×3 or 1×4: `2.4–3.2`
  - 2×3: `1.6–2.2`
  - 3×3: `1.0–1.4`
- **PIL reliability:** ✅ RELIABLE — `img.size[0] / img.size[1]`.
- **L1 vs L2:** L1 wins, ±10% tolerance. Don't sub-pixel lock.

### B. PER-PANEL aspect (W/H of one panel's data area)

This is independent of figure aspect and arguably MORE important for the
"refined" feel — even with correct figure aspect, wrong hspace/wspace produces
panels that read as flat or squished.

**Per the Measurement humility meta-principle: this is an eyeball + iterate
property, NOT a code-measurement property.** Panel-bbox detection heuristics
are brittle (the same image can give 0.98 vs 1.92 across two heuristics).
False-precision PIL output on this property is worse than
honest eyeballing.

- **Most likely classes (eyeball the reference into ONE of these):**
  - **Near golden ratio** (1.4–1.7) — the default for line plots. Most paper
    figures sit here. Panels look "naturally proportioned" — clearly wider
    than tall, but not flat.
  - **Near square** (0.9–1.2) — scatter plots, heatmaps; sometimes line plots
    in dense multi-panel grids. Panels look balanced, neither wide nor tall.
  - **Tall** (0.6–0.9) — bar charts, stacked-area, vertical-emphasis panels.
    Panels are clearly taller than wide.
  - **Very wide** (1.7+) — usually a smell. Suggests excess hspace or wspace.
    The user-facing word is "扁扁" — flat-wide. Avoid by default.

- **Workflow (constrain → perceive → render → adjust):**
  1. Look at the reference panels. Which class above? Pick by eye.
  2. Pick a target value in the middle of that class (e.g. 1.55 for golden).
  3. Don't try to PIL-measure the reference's exact value — heuristics are
     brittle for this and the false-precision is harmful.
  4. Render. Look at output. Compare to reference at same scale by eye.
  5. If output reads in a different class than reference (e.g. you targeted
     golden but rendered output reads as wide-flat 1.9), adjust margins /
     hspace / figsize to pull back into class. Iterate.

- **Dependencies (informational, not prescriptive):**
  - per_panel_aspect ≈ figure_aspect × n_rows / n_cols × M, where M is a
    correction for margins/wspace/hspace (typically 0.7–1.0).
  - For 2×3 grids with figure aspect ≈ 1.95: per-panel aspect lands around
    1.0–1.3 with normal margins; getting golden 1.5–1.7 needs tighter top/
    bottom margins or tighter hspace.

- **PIL reliability:** ❌ **UNRELIABLE in practice.** Panel-bbox detection
  heuristics produce inconsistent values across image variants. **Use eyeball
  classification.** Code measurement of full-image aspect (property A above)
  is reliable; per-panel aspect is not.

- **L1 vs L2:**
  - **L1-perceived (eyeball) wins** for class identification.
  - L2 menu provides the class options.
  - Record in notes as `[L1-perceived]` per the Measurement humility section,
    not `[L1-PIL]`.
  - Default class when truly ambiguous: near golden ratio (1.5–1.7).

### Critical: hspace is NOT for "making room for panel titles"

A common bug is reasoning "each row has its own panel
titles, so hspace must be larger." This is wrong, and it inflates per-panel
aspect by 30–50%, producing flat panels. Observed failure: a draft increased
hspace for row titles even though title `pad` would have reserved the needed
points without flattening the panels.

- **Panel titles** use the `pad=` parameter on `set_title(...)` — units are
  matplotlib **points** (typically 4–8 pt). At 180 dpi, 6 pt ≈ 15 px ≈ a small
  fraction of a panel's height.
- **`hspace` fraction** is the gap *between the bottom of one panel's axes and
  the top of the next panel's axes*. It needs only enough room for the bottom
  panel's xlabel + a comfortable visual gap.
- **Right values:** hspace `0.18–0.30` covers most cases. hspace `0.40+` is a
  smell — investigate whether you're confusing pad with hspace.
- **Symptom check:** if your per-panel aspect (W/H) is > 1.7 and your figure
  aspect matches the reference, the cause is almost certainly hspace inflation.

### Why "扁扁的" reads as un-paper-like (the aesthetic claim)

Per-panel aspect 1.7+ tends to dilute the visual density of the data — line
slopes look gentler, peaks compress, spatial patterns flatten. Top-conference
figures bias toward 1.4–1.7 because that range gives line plots enough vertical
room for slope readability while keeping the figure compact. Square-ish panels
(0.9–1.2) are the alternative for very dense data or heatmaps. Anything wider
than 1.7 reads as a slide-deck panel, not a paper panel.

### The coupling between figure aspect and per-panel aspect (key constraint)

These two are NOT independent. For an n_rows × n_cols grid with reasonable
margins:

```
per_panel_aspect ≈ figure_aspect × n_rows / n_cols × M
```

where M is a correction factor for margins, wspace, hspace (typically 0.7–1.0).

For a 2×3 grid:
- figure aspect 1.95 → per-panel aspect ≈ 1.95 × 2/3 × M ≈ 1.3 × M ≈ 1.0–1.3
  (with reasonable margins). Achieving panel aspect 1.5–1.7 requires margins
  that DON'T eat vertical space (small top/bottom + small hspace).
- figure aspect 2.5 → per-panel aspect ≈ 2.5 × 2/3 × M ≈ 1.7 × M ≈ 1.5–1.7

So if the L1 reference has figure aspect 1.95 AND per-panel aspect 1.5, that
implicitly requires tight margins (top close to 0.95, bottom close to 0.05,
hspace 0.20 or less). That's the bargain.

**The trade-off the doer must understand:**

| If you prioritize... | What gives |
| --- | --- |
| Match reference figure aspect AND per-panel aspect simultaneously | Need to match reference's full margin/spacing recipe (hard if our data needs different label-band headroom) |
| Match figure aspect with relaxed per-panel aspect (1.7+) | "Flat-wide" panels — paper register slipping toward slide-deck |
| Match per-panel aspect by deviating from figure aspect (using ±10% of L1) | Can get close but may exceed band; document the trade |
| Match per-panel aspect by taller/wider figure outright | Best when our data has different label density than reference |

**The L2 default when our data forces a choice:** prioritize per-panel aspect in
the 1.4–1.7 band over exact figure aspect. A figure with golden-ratio panels and
figure aspect 1.6 reads more paper-like than a figure with matching figure
aspect 1.95 and per-panel aspect 1.9. Slightly deviating from L1's figure aspect
(within ±10% tolerance) is usually fine; a 1.9+ per-panel aspect is usually a smell.

## Markers

- **Most likely classes:**
  - Filled circle: diameter `4–8pt`, no edge
  - X-cross (for baseline / "before" series): `5–7pt`, line width `1.0–1.5pt`
  - Filled square / triangle: `5–8pt`
- **Dependencies:** marker size should scale with line width (`marker ≈ 1.5 × line_pt`
  is a typical heuristic).
- **PIL reliability:** ✅ **RELIABLE** for diameter (filled region), ⚠️ for edge.
- **L1 vs L2:** L1 wins for shape and approximate diameter; L2 default if the marker
  is too small in the reference to discern (≤3 px).

## Color palette

- **Reference's PIL-sampled palette is always PRIMARY.** Sample the line/marker
  CENTER (not edge — anti-aliasing distorts edges).
- **Extension menu (when our data has more series than the reference):**
  - Tableau-10 (warm primaries, well-tested on print + projector)
  - Seaborn-deep desaturated by 15% (warm, not garish)
  - ColorBrewer Set2 (qualitative, colorblind-safe)
  - Sequential extensions: viridis / plasma / cividis slices
- **Constraints:**
  - Avoid red+green pairing alone (colorblind-hostile)
  - Maintain hue separation ≥ 30° between adjacent series
- **PIL reliability:** ✅ **RELIABLE** when sampling line/marker fill area. ❌
  unreliable for edge anti-aliasing pixels and for thin lines.
- **L1 vs L2:** L1 wins for series 1..N where N = reference's series count. L2
  extends for series N+1..M.

## Tick marks

- **Most likely classes:**
  - Outward, length `3–5pt` (NeurIPS convention)
  - Length `0` (no tick marks; gridlines do the job — common in modern paper figures)
  - Inward, length `3–4pt` (older convention; reads "1990s scientific paper" — avoid
    by default)
- **Dependencies:** tick padding (text-to-tick gap) should be `4–8pt`.
- **PIL reliability:** ⚠️ partially reliable for length; ❌ for direction at low res.
- **L1 vs L2:** L1 wins; L2 default is `length=0` if reference is ambiguous.

## Legend treatment

### Frame style (which kind of legend)

- **Most likely classes:**
  - Rounded soft-tinted frame (the reference under study uses this — `#adc9e9`
    blue tint, `#eec8b0` orange tint, `boxstyle='round'`)
  - No frame (`frameon=False`) — Nature body figures
  - Inline text labels at line ends (`ax.text` per series)
- **PIL reliability:** ⚠️ partially reliable for frame color; ✅ for presence/position.
- **L1 vs L2:** L1 wins for frame style.

### Internal density

This is the matplotlib parameter set that controls how packed the legend looks.
Per the **Compactness preference (meta)** above, default class is `tight`.

- **Most likely classes:**
  - **Tight (paper register, DEFAULT):**
    `handlelength 1.5–2.0`, `handletextpad 0.3–0.4`, `columnspacing 0.8–1.4`,
    `borderpad 0.3–0.4`
  - **Moderate (matplotlib defaults — slide deck / workshop register):**
    `handlelength 2.0`, `handletextpad 0.8`, `columnspacing 2.0`,
    `borderpad 0.4`
  - **Generous (rare in papers):**
    `handlelength 2.5+`, `handletextpad 1.0+`, `columnspacing 2.5+`
- **Dependencies:**
  - `ncol` choice: prefer all entries in a single row when canvas allows;
    the legend reads tighter horizontally than vertically for paper figures.
  - Frame internal padding should NOT be padded out to "look balanced" —
    let the text/glyph contents define the frame size, then add
    `borderpad 0.3–0.4` only.
- **PIL reliability:**
  - ✅ RELIABLE for measuring `legend_bbox / total_legend_ink`. Sample the
    legend band, count ink pixels, compare to bbox area; high density (>20%)
    indicates tight, low density (<8%) indicates loose.
  - Direct measurement of matplotlib param values is not possible from PIL,
    but the *visual outcome* is — that's what matters.
- **L1 vs L2:**
  - L1 grounds the *frame style*. L2 grounds *internal density* (default tight).
  - When the reference's legend appears tight by eye, lock to tight class. When
    ambiguous, default to tight per Compactness preference.

### Common matplotlib snippet — tight-class legend

```python
# Tight-paper-register legend params; do NOT use mpl defaults
leg = ax.legend(
    handlelength=1.8,      # <- tighter than default 2.0
    handletextpad=0.4,     # <- much tighter than default 0.8
    columnspacing=1.0,     # <- much tighter than default 2.0
    borderpad=0.35,        # <- slightly tighter than default 0.4
    ncol=N,                # prefer single-row when canvas allows
    frameon=True,          # frame style per L1
    fancybox=True,         # rounded corners if reference uses them
    edgecolor="<L1-sampled tint>",
    facecolor="white",
)
```

## Circos / chord-diagram density (correlation diagrams)

When the reference is a circular correlation diagram (chord arcs +
multi-tier rim bars + multi-group legend), interior chord-web *density*
is a primary L1 anchor — a sparse star pattern reads as categorically
different from a dense crisscrossing web even if palette + rim + legend
all match.

- **Most likely classes:**
  - **Sparse named-only:** ~10-30 chords, only explicitly correlated pairs
    drawn. Reads as "schematic." Match if the reference is schematic.
  - **Dense full-pairwise:** ~200-500+ chords, every observable pair drawn
    (named correlations bold/colored on top of weak-correlation
    background). Reads as "data-rich." Required for HEPfit-style
    references with visibly busy interiors.
- **Range / dependencies:** count the reference's visible chords by eye
  in one quadrant and multiply ×4. If the total exceeds ~80, you're in
  the dense class — synthesize unnamed pairs to fill density visually.
- **Per-chord visual weight (dense class):** `linewidth ≥ 0.7`,
  `alpha ≥ 0.5`. Muted-grey filler at `lw=0.4 alpha=0.2` is too thin to
  register against white background. **Color the filler too** (lower-
  saturation venue palette is fine) rather than greying it — the eye
  tracks colored arcs, so grey filler reads as empty interior even when
  count is high.
- **Rim tiers:** dense-class references typically show 2-3 concentric
  rim bands (outer color band + middle bars + inner stratified bars).
  Match tier count, not exact widths.
- **PIL reliability:** ❌ unreliable for chord count (anti-aliased thin
  arcs blend into noise). Eyeball-classify.
- **L1 vs L2:** L1 wins for class (dense vs sparse). L2 informs per-chord
  linewidth/alpha within the chosen class.
- **Common defect:** drawer adds many low-alpha grey filler chords but
  the colored named chords stay sparse. Visual result reads sparse
  because the colored arcs are what the eye tracks.

## Per-point label band (when reference uses stacked numeric labels)

- **Strategy classes:**
  - V2/delta stacked above marker, V1 below: 2 lines above + 1 line below
  - Single-line above only
  - No per-point labels (just legend + line)
- **Headroom requirement:** label band height in display points = `(lines × annot_pt
  + (lines-1) × line_gap_pt + pad)` → translate to data units via
  `ax.transData.inverted()`. ylim_top must cover this.
- **Dependencies:** entirely data-shape-dependent. **Do not copy the reference's
  ylim numbers.** Compute fresh.
- **L1 vs L2:** L1 wins for which strategy; L2 wins for the headroom arithmetic.

---

## Editing this file

When extending: add new sections under appropriate categories. Keep the per-section
template:

```
- Most likely classes
- Range / dependencies
- PIL reliability
- L1 vs L2 precedence
```

When a new reference image surfaces a property that doesn't fit existing classes,
add a new class to the relevant section rather than rewriting the rule.
