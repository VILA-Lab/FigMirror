## Status (post-spike, 2026-05-06)

**Stage 1 spike complete.** 6 manual loop runs (v8 → v11-rerun-2) + 12 library
patches produced a working v11 prompt set, consolidated as canonical asset at
`resources/prompts/figure-style-copier.md`. Fresh-agent rerun (v11-rerun-2)
reached the first natural `ship` verdict at iter 4 with 0 reproductions of 5
originally-named bugs. See `openspec/sessions/phase0-spike-and-product-principles.md`
for the journey.

This document was written **before** the spike with intentional flexibility
about specific values; sections below preserve that original framing where it
still holds, and append two new sections — `## Decisions added post-spike (v11
framework)` and `## Product positioning principles` — that capture what the
spike actually produced.

## Context

本设计承接 `proposal.md` 的 Phase 0 范围：在 Claude Code skill 形态下落地一个「脏数据 + 参考图 → PDF + 内联数据脚本」的两阶段交互流程。proposal 给出"做什么 / 为什么"，本文档给出"怎么做"。

~~设计落地之前还有一次 **spike**（在 Claude Code 中手跑一次完整 loop）作为前置验证，spike 数据由用户后续提供。~~ Spike 已完成（见上方 Status）；本设计原本给出的具体策略与默认值（如硬轮数上限的初值）已在 spike 后据实调整，**回路骨架与各项策略的"形状"基本如设计**，但 spike 引入了 design 时未预料到的 L1/L2 grounding 框架与 aesthetic library 抽象（见 `## Decisions added post-spike`）。

环境约束：
- 本机已有兄弟项目实现了对 GPT (OpenAI) API 的调用基础设施，评估期可复用做横向对比。
- Claude 的调用主路径是 Claude Code（必要时 Codex 备选）。
- 多模态能力假设：Claude Sonnet/Opus、GPT-4o 级模型的图像理解已可支撑此 loop；不假设特殊微调或专用模型。

## Goals / Non-Goals

**Goals:**

- 定义两阶段交互的边界与显式过渡协议。
- 定义内层 loop 的双轨停机机制（agent 自判 + 硬轮数上限）。
- 定义参考图系列数与用户数据系列数不一致时的配色策略（含 conditional 第二路径）。
- 定义参考图模糊时的检测与提示路径（不做自动超分、不阻塞流程）。
- 定义输出脚本中"data sector"的位置与边界。
- 在不引入自定义工具的前提下，让 skill 走通 Claude Code 现有的 Read / Write / Bash 能力。

**Non-Goals:**

- 不做前端、不做 slot 渐进披露 UI（Phase 2）。
- 不做 LaTeX table 生成（平行 capability，后续单独提案）。
- 不做风格 preset 库 / 用户级风格管理（更后期）。
- 不为用户管理 matplotlib / Python 环境，不做依赖隔离或沙箱。
- 不对低分辨率截图做自动超分。
- 不在 Phase 0 强求 Codex 同等支持——Codex 的 loop 形态留作 Open Question。
- 不在产品端做"自动判断阶段过渡"——阶段切换始终由用户显式 gate。

## Decisions

### 1. 内层 loop 停机 = agent 自判 ∧ 硬轮数上限（双轨）

每一轮（render → 对照 reference → 改写 → 再 render）末尾，agent 必须显式输出一个自判标签：`continue` 或 `converged`。

- 任一轮 agent 输出 `converged` → 停。
- 累计达到硬上限仍未 `converged` → 停（取当前最好一版）。
- 硬上限初值取 ~~5~~ **6**（v9 后调，v11-rerun-2 在 iter 4 自然 ship 验证 6 足够；保留 1-2 轮 margin）。

**理由：**
- 自判允许早停（多数情况下 2–3 轮就够），节省 token 与时间。
- 硬上限兜底，避免 agent 在两个错误间震荡或长尾不收敛。
- 两者互补；只用其一都各有失败模式。

**备选与否决：**
- "仅 agent 自判"——可能永不收敛或过早自满。
- "仅硬上限"——浪费轮数，且没有"已经够好了"的早停信号。

### 2. Stage 1 → Stage 2 过渡 = 用户显式 gate

每次内层 loop 停机后，agent **必须**向用户发出过渡询问，文案大致为：

> 我认为风格已经迁移到位了。您觉得整体风格还有哪些需要调整的地方？如果您觉得 OK，我们进入图细节微调阶段（坐标轴、刻度、legend 文字等）。

用户后续输入的处置：
- 用户给出**风格层面的新指令**（"配色再深一点"、"字体换衬线"等）→ 仍在 Stage 1，继续触发新一轮 loop。
- 用户给出**接受性回答**（"OK"、"可以"、"进入下一步"等）→ 显式进入 Stage 2。
- agent 不主动猜——遇到模糊回答应再问一次。

**理由：**
- 阶段过渡是产品体验的关键节点，由 agent 自判极易出现"提前下班"或"不肯下班"两种坏模式。
- 显式 gate 实现成本极低、可解释性强、用户感知清晰。

**备选与否决：**
- "agent 启发式判断用户语气"——脆弱、不可解释、bug 难复现，否决。

### 3. 配色策略 = 两阶段实施，第二条路 conditional

**第一条路（先做、必做）：纯 prompt optimization**

在 system prompt 中写明配色原则，例如：

> 当用户数据的系列数 ≠ 参考图中的系列数时：
> - 优先使用从参考图中提取的色板作为 base（按出现顺序）。
> - 多余的系列从同一 hue family 中扩展（同色系不同明度 / 邻近 hue），不要引入与参考图色系冲突的颜色。
> - 系列数不足时直接截取参考图色板的前 N 个。

通过 spike + prompt iteration 在主流多模态模型上调通。

**第二条路（conditional）：调色板模板 RAG**

如果第一条路在主流模型上效果不达标，引入：
- 一个手工/半自动收集的色卡库（每条目：色板 + 风格标签 + 一段描述）。
- 检索：把 reference 图的视觉特征（dominant colors、 hue 分布）做近邻匹配，取 top-K 注入 prompt 作为 in-context 示例。
- "比 RAG 更聪明一些"的细化（例如混合检索 + agent 自判选取）留到那时再决定。

**决策门槛：** spike 后基于"prompt-only 路线在 Claude / GPT 上的输出质量"决定是否启动第二条路。

**理由：** 复杂度先低后高；如果便宜路线已能满足，不付 RAG 的运营/数据成本。

### 4. 参考图模糊检测 = CoT 自评 + 终端提示，非阻塞

skill 在 system prompt 中要求 agent 在每轮内层 loop 的推理段显式输出一个图像清晰度 self-tag：`clear` / `ok` / `blurry`。

harness 侧规则：
- 若连续 ≥2 轮 self-tag 为 `blurry`，在下一次与用户交互时附上提示：
  > 您提供的参考图分辨率较低，agent 在辨别字体 / 线宽 / 边框等细节时比较吃力。建议提供更高分辨率的版本以提升风格还原度。
- **不阻塞流程**——agent 继续尽力而为。
- 不做自动超分、不做硬性 DPI / 像素阈值判断。

**理由：**
- 把判断委托给 agent 本身的视觉评估，比硬指标更通用（vector PDF 截图分辨率高但视觉细节也可能丢失，硬指标会误伤/漏判）。
- 提示是建议性的，不打断用户当前流程。

**备选与否决：**
- "硬阈值（小于 X DPI 直接拒）"——误伤面太大且对模糊定义粗糙。
- "自动超分"——引入额外模型与依赖，与 Phase 0 轻量化原则冲突。

### 5. 输出脚本结构 = 显式 data sector + 内联数据

输出 `.py` 顶部带显式标识：

```python
# === DATA SECTOR (edit here) ===
# All raw data lives in this region as inline literals.
# Modify here to update the figure without re-running the agent.
...
# === END DATA SECTOR ===

# --- styling, plotting code below ---
```

agent 在 data sector 内：
- 把用户的脏 paste 解析后以 numpy / pandas / list 字面量形式落下。
- 必要的轻量预处理（重命名、去单位、reshape）也写在该 sector 内并就地完成。
- sector 之外的代码只引用 sector 内已命名的变量。

**理由：**
- 用户改数据只动 sector，不需重跑 agent。
- 脚本完全可复现：发给同事、贴到 supplementary，不依赖外部 CSV 路径。
- 边界明显，前端 / 工具未来可基于这段 marker 做 diff、做"只重新执行 sector 之外的部分"等优化。

**备选与否决：**
- "外部 CSV 引用"——用户改数据要动两个文件，且对脏 paste 这条主路径不友好。

### 6. Runtime / harness 选择

- **canonical 形态**：`.claude/skills/figure-style-copier/SKILL.md`，依赖 Claude Code 已有的 Read（图）/ Write（脚本）/ Bash（跑 matplotlib）能力，**不引入自定义工具**。
- **开发与评估期**：可以用 sub-agent 调度做横向对比；本机兄弟项目已有 GPT 调用基础设施可复用，将同一个 prompt + 输入扔给 Claude Code 与 GPT-4o 等观察差异（数据由 spike 阶段提供）。
- **Codex 形态**：留 Open Question；Codex 的工具调用与多轮 image hold 语义与 Claude Code 不同，需要单独评估能否同结构实现。

**理由：**
- 越少自定义工具，skill 越可移植、越易迁移到 Codex / 其它 harness。
- 对比评估是为了验证"loop 在主流模型上都 work"的设计前提。

### 7. 数据 echo 确认（数据解析失败的 escape hatch）

stage 1 开始时，agent 完成数据 normalize 后**先把读到的结构 echo 给用户**：

> 我读到的数据是：3 个分组（method = A / B / C），每组 100 个点，列含 `epoch` / `loss`。要画的是 epoch-loss 折线图按 method 分色。对吗？

用户确认后才进入第一轮渲染。**不**让 agent 直接闷头画——脏数据下错读结构是高频失败模式。

**理由：** 错读数据会让后续 N 轮 loop 全部白做；echo 一次的成本远低于错画数轮。

---

## Decisions added post-spike (v11 framework)

These are the design moves that emerged during 6 spike runs and were not
predicted in the pre-spike design above. They are the actual implementation —
captured here for permanence.

### 8. Doer / Reviewer separation as the loop primitive

The single-agent self-judging loop in pre-spike Decision 1 evolved into a
two-persona pattern under sustained iteration:

- **Doer (`figure-illustrator`)**: produces matplotlib script + rendered PNG +
  iter notes. Owns all detail-level work (data fidelity, matplotlib mechanics,
  pixel measurement).
- **Reviewer (`figure-critic`)**: a fresh-context Bash subprocess
  (`claude -p --model opus`) with bounded PIL access. Reads only the rendered
  PNG + reference + library + prior audit. Outputs a strict JSON audit.
- **Orchestrator (the doer's session, acting as harness)**: shuttles artifacts
  between, applies the decision rule, manages iters.

The fresh-context reviewer is structurally important — it prevents the doer's
reasoning chain from biasing the audit. v9-onward all run this pattern.

### 9. Anchor-based preservation against monotonic drift

v9 exposed a failure mode: even with floor checks, a single-agent loop drifts
monotonically on properties the reviewer happens not to mention each round.
Spike data showed aspect ratio drifting 1.95 → 1.55 (-21%) over 5 iters, and
spine count silently flipping from L+B-only to all-4.

v10 fix: reviewer schema gains an explicit `anchor.what_is_right[]` array (3-7
items per audit) listing properties the doer **must NOT change** in subsequent
iters. The orchestrator forwards this as a hard preserve list. This eliminated
both drift cases on the same inputs.

### 10. L1 / L2 / L3 grounding hierarchy (the v11 reframe)

Every claim made by either doer or reviewer must trace back to one of:

- **L1 — the user-supplied reference image.** Highest authority. Has two sub-modes:
  - **L1-PIL**: code-measured (palette of large filled regions, full-image
    aspect, gridline direction via row+col profile). Use when the heuristic
    is trivial and unambiguous.
  - **L1-perceived**: eyeballed (font family, panel-aspect class, density
    gestalt). Use when code measurement is brittle.
- **L2 — the aesthetic library** (paper-figure conventions, NeurIPS / ICML /
  ICLR / Nature family). Used as fallback / sanity backstop / extension menu.
- **L3 — the model's own opinion.** **Disallowed.** Reviewers and doers must
  cite L1 or L2 for every claim; pure opinion is treated as noise.

Anchor entries are prefixed `[L1-PIL]` / `[L1-perceived]` / `[L2]` /
`[L1+L2]` to make the source explicit and falsifiable.

### 11. The aesthetic library as L2, structured + living

`resources/prompts/figure-style-copier.md` (consolidated) and per-section
under `spike-results/prompts/v11_aesthetic_library.md` (history). Contains
**3 meta-principles + 12 property sections**, each section structured as:

- Most-likely classes (categorical menu, not single value)
- Range / dependencies
- PIL reliability (`✅ reliable` / `⚠️ conditionally reliable` / `❌ unreliable`)
- L1 vs L2 precedence rule

The library evolved through 12 patches in response to observed spike failures.
Each patch added a **new property dimension** (not just a new value) — e.g.
`gridline.direction`, `inter-panel spacing class`, `legend internal density`,
`per-panel aspect class`. The pattern: user observation → diagnose missing
dimension → add dimension to library → re-run → next observation.

### 12. The 3 meta-principles (cross-cutting library rules)

1. **Compactness preference** — top-conference figures are tight, not airy.
   Default `tight` class for any density-related property (inter-panel spacing,
   legend density, label band, margins). Matplotlib defaults all sit in the
   `moderate` class — falling back to defaults gives non-paper register.
2. **Hairline calibration: visible-but-recessive** — fine elements (spines,
   gridlines, ticks) need to be visible AND recessive. Pick the **literal middle**
   of L2 ranges, not the pale extreme (invisible) or dark extreme (competes with
   data). Apply to color, width, alpha jointly.
3. **Measurement humility** — code measurement reliability is heuristic-conditional,
   not categorical. False precision is a worse error mode than acknowledged
   uncertainty. For brittle heuristics (panel bbox, hairline width, font family),
   prefer eyeball + iterate over code + lock. The human workflow is `constrain
   (L2 menu) → perceive (L1 eyeball) → render → adjust`.

### 13. Reviewer as bounded-PIL, JSON-only

The reviewer subprocess gets `--allowedTools "Read Bash"` and `--disallowedTools
"Edit Write NotebookEdit Agent"`. Bash is for `python -c` PIL measurement only.
Output is strict JSON parsed via `json.loads`. The schema:

```
{
  "iter": int,
  "anchor": {
    "what_is_right": [<L1/L2-prefixed strings>, ...],
    "measurements": {<key: value>}
  },
  "quality_floor": {
    "passed": bool,
    "violation_kinds": [<enum>, ...],
    "summary": str
  },
  "fidelity": {
    "verdict": "ship" | "close" | "off",
    "paragraph": str
  },
  "focus_themes": [<L1/L2-prefixed strings>, ≤5]
}
```

`focus_themes` cap = 5. `quality_floor.violation_kinds` is a closed enum of 9
named floor-violations (text overlaps tick / title / text-in-axes; label clipped;
axis off-canvas; illegible at print size; default matplotlib aesthetic;
font_family_mismatch; font_weight_too_heavy).

### 14. Decision rule: ship/close/off + floor independence

Orchestrator's per-iter decision is a 3-input function:

- `floor.passed == false` → continue (or break at MAX_ITERS-1)
- `verdict == "off"` → continue
- `verdict == "close"` AND budget remains → continue
- `verdict == "ship"` AND floor passed → ACCEPT, stop

No score arithmetic. The floor is independent of the verdict — a figure can
score `ship` on style but the floor still gates it.

### 15. Damping rule: no opposite-direction themes

The reviewer is fresh-context per iter and reads the prior audit. New rule
(v10): if a prior `focus_theme` pushed the doer in direction X and the doer
moved in X, the reviewer **must NOT** write a theme pushing the opposite
direction. Either accept the new state, or recommend continued movement in
X. Prevents the v9-observed `bolder ↔ lighten` oscillation.

### 16. Per-property reliability annotations are conditional, not categorical

Library sections that say `✅ PIL reliable` are reliable **only when applied to
the right region with the right heuristic**. Brittle points called out
explicitly:

- Panel-bbox detection picks up text frames / legend pills if naive.
- Hairline width measurement is dominated by anti-alias halo.
- Inter-panel gutter ratio depends on which y-band you scan.
- Min-along-line spine sampling requires the strip to be on the actual spine.

When in doubt, the doer falls back to L2 class menu + eyeball + iterate.

---

## Product positioning principles

These emerged from spike post-mortem and are **not technical rules** — they're
product-level decisions that shape what we are vs are not. Future skill.md
should carry them.

### P1. User contract on input quality

Reference is a paper figure screenshot at **normal-reading size** (the user
should not feed in a tiny thumbnail; "half a MacBook screen" is the rough
benchmark). We do not engineer for low-resolution adversarial inputs. Users
who want good output give us readable input.

### P2. The 80/20 envelope

We optimize for the **median user**: someone copying a mid-to-high quality
top-conference figure. Adversarial cases (deliberately-bad references,
non-paper-style inputs) are out of envelope. We don't punish ourselves to
handle edge cases that 80% of users don't have.

### P3. Opinionated stylist, not faithful copyist

This is the core product positioning. We **do not faithfully reproduce** every
property of the reference. Specifically:

- We override `Compactness preference` defaults even when reference is loose
  (most references are loose because matplotlib's defaults are loose; we tighten).
- We fix floor-level violations even when the reference has them (some references
  have number/marker overlap; we resolve, we don't reproduce the bug).
- We pick the literal middle of L2 ranges even when reference is at an extreme
  (e.g. gridline color: ref measures `#ebebeb` pale-extreme, we pick `#e0e0e0`
  mid-class for matplotlib output).

The user can ask us to relax these defaults; we do not relax them automatically
based on a loose reference. **We are an opinionated stylist with taste; the user
can override.**

### P4. Multi-figure consistency via serial chain, not internal state

If a user wants 3 figures in the same paper to share style, the framework
relies on **transitivity**: if `transfer(A → B)` is 90% accurate and
`transfer(A → C)` is 90% accurate, then B and C are ≈ 80% mutually consistent —
acceptable for paper context.

The one genuine non-determinism is series-count extension (3-line ref →
4-line target invokes the palette extension menu, which may pick different
hues than 3-line ref → 5-line target). The product workaround: **draw the
4-line variant first, then use that 4-line image as reference for the next
4-line transfer**. This is a UX teaching question (we cannot enforce the
chain), but the product surface should communicate it.

The framework explicitly **does NOT** maintain cross-figure state. That's a
phase-2 / hosted-product feature.

### P5. Stress-test envelope: mid-to-high paper figures, no adversarial

Concrete scope of what we test against: bar/line/scatter chart types, std
shaded bands, multi-panel grids, series-count extension within reason.
Out of envelope: hand-drawn figures, infographics, pie charts in NeurIPS
(a pie chart in a paper IS the bug, the figure-style-copier shouldn't enable it).

## Risks / Trade-offs

Pre-spike risks (most still hold post-spike):

- **[loop oscillation / non-convergence]** → MAX_ITERS hard cap is the backstop;
  damping rule (post-spike Decision 15) prevents `bolder ↔ lighten`-type
  oscillation by forbidding opposite-direction themes between consecutive
  reviewer audits.
- **[font unavailable on user's machine]** → matplotlib's automatic fallback;
  library Type section now provides explicit per-class fallback chains
  (e.g. Times → Liberation Serif → DejaVu Serif).
- **[output PDF carries Type 3 fonts and gets rejected by venue submission system]**
  → boilerplate enforces `matplotlib.rcParams['pdf.fonttype'] = 42` in every
  generated script.
- **[model vision capability variance across vendors]** → "model-swappable" was
  preserved as a design principle; the spike was Claude-only so far, with
  GPT-4o / cross-vendor evaluation deferred to a separate change.
- **[dirty-data normalization failure]** → Decision 7's echo-confirmation step
  is the escape hatch; not exercised in the spike (inputs were already-clean
  CSV-style numeric data). Re-flagged as Open Question + tasks.md pending item.
- **[security of generated code]** → Phase 0 executes on the user's local
  machine; equivalent trust boundary to "user runs Python themselves." Hosted
  variants would need sandboxing — separate concern.
- **[colorblind / print-friendliness implicit constraints]** → library's
  Color palette extension menu lists colorblind-safe options
  (ColorBrewer Set2); not enforced by default, user-toggleable later.

New risks discovered post-spike:

- **[reviewer-side false-affirmation + heuristic disagreement]** — the failure mode
  v11-rerun-2 actually exhibited is more subtle than "both skip verification" and worth
  characterizing precisely. The doer DID run PIL on gridline direction (`notes_iter0.md`
  records a tightened interior-band scan that excluded data-line columns and returned
  "horizontal-only: 3 dark cols only, no continuous vertical lines"). Based on that
  L1-PIL evidence the doer set `ax.xaxis.grid(False)` — a defensible call given its
  measurement. **The reviewer's iter-4 audit then claimed `[L1-perceived+L2] Gridlines
  remain present in BOTH directions` — affirming a property the draft did not have.**
  The reviewer did NOT PIL-verify the draft itself; it appears to have inherited the
  prior anchor's claim and the library's "default both" rather than checking the actual
  rendered image.
  Two coupled root causes:
  (i) **PIL heuristic disagreement**: a wide-band scan (including data-area columns)
      detects gridlines + data-line crossings as both vertical-darkness; a narrow
      interior-band scan filters out data lines but loses faint gridlines that pass
      through markers. Same image, two valid heuristics, two answers.
  (ii) **Reviewer-side verification gap**: even with a thoughtful doer measurement,
       the reviewer must independently verify on the draft before affirming a
       hairline-element anchor. The reviewer treated library defaults as fact.
  **Mitigation (v11.6 patches 9-10)**: when `figure_iter<N>.py` source code explicitly
  disables a hairline element, reviewer affirmations contradicting that source are
  treated as floor-level reviewer violations; the reviewer must run its own PIL profile
  on the DRAFT (not just rely on library defaults or doer claims) before affirming.
  Doer is also encouraged to record both wide-band and interior-band scan results in
  `notes_iter0.md` when the heuristic could plausibly disagree.
- **[meta-principle over-generalization]** — Measurement humility (v11.5) was supposed to
  be "prefer eyeball over BRITTLE measurement"; agent generalized to "prefer eyeball over
  ALL measurement" and skipped reliable PIL too. **Mitigation**: explicit per-property
  reliability tables in library; HARD RULE wording for cases where PIL is genuinely
  required (gridline direction, full-image aspect).
- **[symmetric anti-pattern needed]** — single-sided anti-patterns ("don't pick pale extreme")
  drive single-sided drift. v11 gridline color ping-ponged `#ededed → #d4d4d4` because
  library only named-and-shamed pale extreme. **Mitigation**: library now writes anti-patterns
  symmetrically (pale extreme AND dark extreme both wrong; pick literal middle).

## Open Questions

Closed by spike:

- ~~**MAX_ITERS specific value**~~ → set to 6 (Decision 1, updated).
- ~~**Whether to launch the second palette path (RAG)**~~ → not started; v11
  library's L2 extension menu (Tableau-10, Seaborn-deep, ColorBrewer Set2)
  handles the spike's needs. RAG path deferred indefinitely until prompt-only
  fails on a real case.
- ~~**Spike protocol itself**~~ → driven by user-supplied inputs from
  `spike-results/.../inputs/` across 6 runs. Documented in
  `spike-results/prompts/v9_findings.md` and per-spike `selection.md`.

Still open:

- **Data-echo interaction form (Decision 7)** — Decision 7 specifies this as a Stage 1 entry path,
  but the v8 → v11-rerun-2 spike never exercised it (inputs were already clean
  CSV-style numeric data, not the dirty terminal-paste case proposal.md describes
  as the main entry). Tasks.md tracks this as a pending Stage 1 polish item;
  it must be exercised on a real dirty-data input before Stage 1 is genuinely
  ready to package as a skill.

- **Codex variant** — same as pre-spike. v7 had a `codex-subagent-v7/` run dir
  but it's gitignored and the comparison was never made. Defer to a separate
  change.
- **Stage 2 boundaries** — Stage 2 has not been built. Currently the loop
  has no Stage 2 protocol; once Stage 1 ships and we exercise it on real
  user flows, Stage 2 boundaries will surface. Defer to separate change.
- **Cross-chart-type generalization (NEW)** — library is line-plot-biased.
  Bar charts, scatter, heatmaps, std bands, and single→grid promotion all
  need stress testing. Pending tests will surface library coverage gaps;
  see `tasks.md` pending list.
