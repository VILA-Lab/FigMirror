<h1 align="center">
  <img src="docs/assets/figmirror-wordmark.svg" alt="FigMirror" width="400">
</h1>

<p align="center">
  <b>FigMirror：让你的数据画出任意论文图的风格。</b><br/>
  选一张参考图，粘贴自己的数据，得到可编辑的 matplotlib 脚本和 camera-ready PDF。
</p>

<p align="center">
  <img src="docs/assets/show.png" alt="FigMirror turns repeated plotting-code edits into a polished paper figure" width="100%">
</p>

<p align="center">
  <a href="#web-ui"><img alt="Web UI" src="https://img.shields.io/badge/Web%20UI-local%20app-2563eb?style=flat&logo=googlechrome&logoColor=white"></a>
  <a href="#codex-skill"><img alt="Codex skill" src="docs/assets/badges/codex.svg"></a>
  <a href="#claude-code-skill"><img alt="Claude Code skill" src="https://img.shields.io/badge/Claude%20Code-skill-d97706?style=flat&logo=anthropic&logoColor=white"></a>
  <a href="https://arxiv.org/abs/2608.28814"><img alt="arXiv paper" src="https://img.shields.io/badge/arXiv-2608.28814-b31b1b?style=flat&logo=arxiv&logoColor=white"></a>
  <a href="https://huggingface.co/spaces/zcahjl3/figcopy-taxonomy-gallery"><img alt="FigMirror gallery" src="https://img.shields.io/badge/Gallery-139%20figures-f59e0b?style=flat&logo=huggingface&logoColor=white"></a>
  <a href="docs/contributing.md"><img alt="Contributions welcome" src="https://img.shields.io/badge/Contributions-welcome-22c55e?style=flat&logo=github&logoColor=white"></a>
</p>

<p align="center">
  <a href="#paper-results">论文与结果</a> |
  <a href="#showcase">展示</a> |
  <a href="#quick-start">快速开始</a> |
  <a href="#how-it-works">工作原理</a> |
  <a href="#star-history">Star 趋势</a> |
  <a href="docs/method.md">Method</a> |
  <a href="docs/contributing.md">Contribute</a>
</p>

<p align="center">
  <a href="README.md">English</a> | <b>中文</b>
</p>

<p align="center">
  <video src="https://github.com/user-attachments/assets/0656009c-77c7-41e5-8423-07c3411aef13" width="900" controls
  muted playsinline></video>
</p>

<h2 id="paper-results">📝 论文与结果</h2>

我们的 [arXiv 预印本](https://arxiv.org/abs/2608.28814)
[*FigMirror: Ground It, Code It, Plot It*](https://arxiv.org/pdf/2608.28814)
已于 2026 年 8 月 28 日发布。论文提出 PlotTwin-Bench，用于参考图条件下的
科学图表风格迁移。

论文报告的主实验评测包含 150 个样本：全部 50 个手工整理参考图，以及从
augmented source 随机抽取的 100 个参考图。FigMirror 在两组上的综合得分分别为
**72.7** 和 **76.4**，均为最高分；两组分开汇报，所有方法均使用 GPT-5.5。

<p align="center">
  <img src="docs/assets/evaluation/main-results.png" alt="论文表 1：FigMirror 与四个 baseline 在 PlotTwin-Bench 上的代码分、视觉分和综合分" width="100%"/>
</p>

<p align="center">
  <sub><b>主实验结果。</b>PlotTwin-Bench 上代码分、视觉分和综合分的对比；FigMirror 在 hand-curated 和 augmented 两组参考图上的综合分均排名第一。</sub>
</p>

<p align="center">
  <img src="docs/assets/evaluation/qualitative-comparison.png" alt="FigMirror、Plot2Code、METAL、ChartGalaxy 和 ChartIR 在两张参考图上的定性比较" width="100%"/>
</p>

<p align="center">
  <sub><b>定性对比。</b>每组在同一 target data 上依次展示参考图、FigMirror 和四个 baseline；A 组考察密集多面板构图，B 组考察带边缘直方图的 joint hexbin 布局。</sub>
</p>

<h2 id="showcase"><img src="docs/assets/icons/showcase.svg" alt="" width="22" height="22" align="absmiddle"> 展示</h2>

FigMirror 使用一张参考图作为风格目标，然后通过 Drawer / Reviewer 迭代循环，把你的数据画成同一类论文图的视觉风格。

<table>
<tr>
<td width="50%" align="center"><b>参考图</b></td>
<td width="50%" align="center"><b>FigMirror 输出</b></td>
</tr>
<tr>
<td><img src="docs/assets/showcase/primary-reference.jpg" alt="reference paper-style figure" width="100%"/></td>
<td><img src="docs/assets/showcase/primary-generated.jpg" alt="FigMirror generated paper-style output" width="100%"/></td>
</tr>
</table>

<table>
<tr>
<td width="25%" align="center"><b>参考图</b></td>
<td width="25%" align="center"><b>FigMirror 输出</b></td>
<td width="25%" align="center"><b>参考图</b></td>
<td width="25%" align="center"><b>FigMirror 输出</b></td>
</tr>
<tr>
<td><img src="docs/assets/showcase/hexbin-joint-reference.png" alt="reference joint hexbin plot" width="100%"/></td>
<td><img src="docs/assets/showcase/hexbin-joint-generated.png" alt="FigMirror generated joint hexbin output" width="100%"/></td>
<td><img src="docs/assets/showcase/waterfall-3d-reference.png" alt="reference 3D waterfall plot" width="100%"/></td>
<td><img src="docs/assets/showcase/waterfall-3d-generated.png" alt="FigMirror generated 3D waterfall output" width="100%"/></td>
</tr>
</table>

<p align="center">
  <a href="https://huggingface.co/spaces/zcahjl3/figcopy-taxonomy-gallery"><img alt="Browse the FigMirror gallery" src="https://img.shields.io/badge/Browse%20gallery-pick%20a%20reference%20and%20play-16a34a?style=flat&logo=huggingface&logoColor=white"></a><br/>
  <sub>手头没有合适参考图？可以先从 139 张论文图、25 类图表家族里挑一张。</sub>
</p>

<h2 id="quick-start"><img src="docs/assets/icons/quick-start.svg" alt="" width="22" height="22" align="absmiddle"> 快速开始</h2>

<h3 id="install-with-your-agent"><img src="docs/assets/icons/agent.svg" alt="" width="18" height="18" align="absmiddle"> 用 Claude / Codex 安装</h3>

如果你已经在 Claude Code 或 Codex 里，直接把下面这句话粘贴给 agent：

```text
Install FigMirror for me: https://github.com/VILA-Lab/FigMirror
```

<h3 id="web-ui"><img src="docs/assets/icons/web-ui.svg" alt="" width="18" height="18" align="absmiddle"> Web UI</h3>

如果你想在浏览器里上传参考图、预览结果、查看迭代过程和继续微调，用这个方式。

如果还没有 `uv`：`python3 -m pip install uv`。

```bash
git clone https://github.com/VILA-Lab/FigMirror.git && cd FigMirror
bash scripts/install.sh
uv run python scripts/figcopy_serve.py --workspace .artifacts/figmirror-workspace --backend codex
```

把 `--backend codex` 换成 `--backend claude`，即可通过 Claude Code 运行同一个 Web UI。

打开 `http://127.0.0.1:8765/`。

安装器会自动识别 Claude Code 和 Codex。两种 harness 都会安装
`figmirror` skill，以及当前 role-separated 算法使用的
`figmirror-drawer` 和 `figmirror-reviewer` custom agents。

<a id="codex-skill"></a>
<a id="claude-code-skill"></a>

<h3 id="skill-only"><img src="docs/assets/icons/skill.svg" alt="" width="18" height="18" align="absmiddle"> 只安装 Skill</h3>

如果你只想在 agent 里使用 FigMirror，不需要 Web UI，用这个方式。
skill 内的 Python helpers 通过 `uv` 运行；如果尚未安装，先执行
`python3 -m pip install uv`。
在共享机器或 root 分区较小的机器上，首次运行前先执行 `df -h`，并把
`UV_CACHE_DIR` 指向最大非 root 可写文件系统中的用户目录。

```bash
curl -fsSL https://raw.githubusercontent.com/VILA-Lab/FigMirror/main/scripts/install.sh | bash
```

然后上传一张论文图截图，粘贴你的数据，并告诉 agent：

```text
Use FigMirror to mirror this figure's style with my data.
```

如果需要手动选择安装目标、使用 Claude 后端或排查问题，请看 [Detailed Install](docs/install.md)。

<h2 id="how-it-works"><img src="docs/assets/icons/how-it-works.svg" alt="" width="22" height="22" align="absmiddle"> 工作原理</h2>

<p align="center">
  <img src="docs/assets/algorithm.png" alt="FigMirror architecture loop and grounded measurement algorithm" width="860"/>
</p>

> FigMirror 示意图。左侧是核心 agent 循环；右侧是 Grounded Measurement。

FigMirror 使用 agentic Drawer-Reviewer 循环。在 Codex 和 Claude Code 中，顶层 Orchestrator 都会把绘图交给 `figmirror-drawer`，把视觉审查交给 `figmirror-reviewer`。Reviewer 对比候选图和参考图，输出带 bounding boxes 的结构化反馈；Orchestrator 再把反馈整理成下一轮 Drawer 使用的标注图和修改说明。两种 harness 共用同一套 decision state machine 和 hard iteration cap，区别只在 subagent transport。

对于 3D 图，FigMirror 会加入 geometry-aware prompting，覆盖 camera、scale、surface、lighting 和 repair checks，帮助循环保留参考图的 3D 构图，同时仍然产出可编辑的 matplotlib 代码。

***Grounded Measurement*** 利用了 computer-use-trained foundation models 的两个能力：*Measurement with Axis* 让模型返回视觉目标的 x/y 坐标；*Resonate with Code* 把这些坐标转成可执行检查，比如裁剪一段线并从像素中读取颜色。

更完整的算法、架构、产品边界和 spec map 见 [docs/method.md](docs/method.md)。Web UI 细节见 [scripts/README_figcopy_serve.md](scripts/README_figcopy_serve.md)。

<h2 id="contributing"><img src="docs/assets/icons/contributing.svg" alt="" width="22" height="22" align="absmiddle"> 贡献</h2>

欢迎给 FigMirror 做贡献！

如果遇到安装问题、bug，或者有希望 FigMirror 学会的图表 case，可以开 issue；如果想贡献 showcase、prompt 改进、UI polish 或小型回归测试，可以直接开 PR。

- **Showcase cases:** 添加 reference/output 对，展示 FigMirror 覆盖更多图表家族的能力。
- **UI polish:** 让 Web 流程更快、更清楚、更容易恢复。
- **Prompt and reviewer quality:** 改进 Drawer / Reviewer 循环，同时不要削弱 grounded rules。
- **Evaluation:** 添加可复现的 cases，用来发现 visual drift、floor violations 和 broken exports。

从 [docs/contributing.md](docs/contributing.md) 开始。适合入门的 PR 包括：添加一个 showcase example，改进一个 Web UI 交互，收紧安装文档，或者给 runner behavior 加一个小回归测试。

<h2 id="star-history"><img src="docs/assets/icons/star-history.svg" alt="" width="22" height="22" align="absmiddle"> Star 趋势</h2>

<p align="center">
  <a href="https://star-history.com/#vila-lab/figmirror&Date">
    <img alt="FigMirror GitHub star history chart" src="https://api.star-history.com/svg?repos=vila-lab/figmirror&type=Date" width="100%">
  </a>
</p>

<p align="center">
  <sub>图表由 Star History 基于 GitHub stargazer 数据动态渲染，会随着仓库增长持续更新。</sub>
</p>

<h2 id="roadmap"><img src="docs/assets/icons/roadmap.svg" alt="" width="22" height="22" align="absmiddle"> Roadmap</h2>

- [x] 将 reference-to-figure loop 发布为 Codex 和 Claude Code skills。
- [x] 添加本地 Web UI，支持上传、浏览迭代和继续 refinement。
- [x] 发布 139 张图的 gallery，让用户不需要自己到处找参考图。
- [ ] 定义 prompt-contribution benchmark verifier，用固定 reference/data cases 比较 prompt 改动。
- [ ] 发布可复现的 PlotTwin-Bench 评测包，包含参考图、目标数据、生成结果和 evaluation metadata。
- [x] 发布包含 scorer protocol、benchmark design、baselines 和 analysis 的 arXiv 论文。

<h2 id="citation">📚 Citation</h2>

如果 FigMirror 对你的工作有帮助，欢迎引用：

```bibtex
@misc{zhao2026figmirrorgrounditcode,
      title={FigMirror: Ground It, Code It, Plot It},
      author={Xiaohan Zhao and Jiacheng Liu and Yaxin Luo and Zhiqiang Shen},
      year={2026},
      eprint={2608.28814},
      archivePrefix={arXiv},
      primaryClass={cs.CV},
      url={https://arxiv.org/abs/2608.28814},
}
```
