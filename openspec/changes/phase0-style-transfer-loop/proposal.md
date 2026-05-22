## Why

读论文时看到顶会图、想"我也要这种风格"，是一个真实存在的高频需求。现有路径都不理想：

- 手动用 matplotlib 复刻样式（rcParams、spine、字体、配色…）耗时；
- Origin 这类传统绘图工具要求工整数据，并且风格库远不及顶会论文的多样性；
- 直接让 LLM "看图出代码" 一次性产出，风格往往抓不准，需要多轮人工纠正。

经验观察：ChatGPT Pro 在内部「画图 → 看自己画出的图 → 与 reference 对比 → 改写代码 → 重画」这个回路上效果明显好于 Claude 的 chat 接口。我们的赌注是：**这个回路在 Claude Code（具备真实 code execution 与 vision）里至少同样好、且天然就是一个 skill 的形态**——一个 system prompt + 若干工具约定 + 多轮对话即可承载。

Phase 0 的目标是把这个最小核做出来跑通，之后再向 CLI、web app、hosted 产品逐级演进。本提案只覆盖 Phase 0，后续阶段（含前端 slot 式渐进披露的微调 UI）将作为后续 change 单独提出。

## What Changes

- 新增一个 Claude Code skill：输入「一份脏数据 + 一张参考图」，输出「一份 PDF + 一份可复现的 matplotlib 脚本」。
- skill 内部驱动一个**两阶段交互流程**：
  - **阶段 1：纯风格迁移。** agent 先在内部自循环（render → 对照 reference → 修订代码 → 再 render），自判收敛后向用户征求风格层面的反馈，直到用户认可整体风格。
  - **阶段 2：细节微调。** 用户确认风格后才进入此阶段，针对图自身的细节（坐标轴 scale、刻度、legend 文字、字号等）开展自然语言驱动的微调对话。两阶段之间有显式过渡，不混在同一轮里跳跃。
- **输入端宽容：** 用户从 terminal 复制下来、带 `|` 分隔符的脏文本也接受；agent 通过自然语言提示或在脚本中写预处理代码来 normalize。用户提供的风格指令同样可以模糊（"再大一些"），不要求精确数值。
- **输出脚本中数据内联**在专门的"data sector"段落里，便于用户后续直接编辑数据。不依赖外部 CSV 文件。
- **matplotlib 运行在用户本地**，推荐最新版本；skill 不为环境兼容性背责。
- **配色策略**：当用户数据的系列数 ≠ 参考图系列数时，由 agent 智能选择与参考图整体色系一致的扩展色——具体策略（纯 prompt vs 参考调色板模板）在 design.md 中决定。
- **参考图分辨率宽容**：agent 应在低分辨率截图下尽力而为；不在 Phase 0 引入显式分辨率检测（该能力归属 Phase 2 前端）。
- **数据/参考冲突时以用户数据为先**：例如 reference 是 log scale、用户数据线性更适合，不强制照抄 reference；用户若有特定 scale 需求，会在阶段 2 自行说明。
- **LaTeX table 不在 Phase 0 范围**——它是平行 capability，后续单独提案。
- **先做 spike 再写 skill**：在落 skill 代码之前，需要在 Claude Code 中手动跑通一次完整 loop，验证回路质量符合预期；spike 结论写入后续 design.md。

## Capabilities

### New Capabilities

- `figure-style-copier`: 接收「脏数据 + 参考图」，通过两阶段交互流程（agent 内部自循环风格迁移 → 用户主导细节微调）产出顶会风格的 PDF 图与一份数据内联、可复现的 matplotlib 脚本。

### Modified Capabilities

（无——这是仓库中第一个 capability。）

## Impact

- 新增目录 `.claude/skills/figure-style-copier/`，承载 `SKILL.md`（system prompt + 工具约定）与示例 trace。
- 用户侧依赖：本地 Python + matplotlib（推荐最新版）、Claude Code CLI（已有）。
- 不引入网络服务、不引入持久化存储、不修改仓库其他部分。
- 不引入前端代码、不引入构建系统改动；前端相关的渐进披露 UX 留待 Phase 2 单独 change。
- 实现前置条件：一次先行 spike（在 Claude Code 内手动跑完整 loop），其结论是 design.md 的输入。
