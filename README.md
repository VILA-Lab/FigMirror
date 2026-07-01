<h1 align="center">
  <img src="docs/assets/figmirror-wordmark.svg" alt="FigMirror" width="400">
</h1>

<p align="center">
  <b>FigMirror: Plot Your Data in Any Paper's Style.</b><br/>
  Pick a reference figure, paste your data, and get an editable matplotlib script plus a camera-ready PDF.
</p>

<p align="center">
  <img src="docs/assets/show.png" alt="FigMirror turns repeated plotting-code edits into a polished paper figure" width="100%">
</p>

<p align="center">
  <a href="#web-ui"><img alt="Web UI" src="https://img.shields.io/badge/Web%20UI-local%20app-2563eb?style=flat&logo=googlechrome&logoColor=white"></a>
  <a href="#codex-skill"><img alt="Codex skill" src="docs/assets/badges/codex.svg"></a>
  <a href="#claude-code-skill"><img alt="Claude Code skill" src="https://img.shields.io/badge/Claude%20Code-skill-d97706?style=flat&logo=anthropic&logoColor=white"></a>
  <a href="https://huggingface.co/spaces/zcahjl3/figcopy-taxonomy-gallery"><img alt="FigMirror gallery" src="https://img.shields.io/badge/Gallery-139%20figures-f59e0b?style=flat&logo=huggingface&logoColor=white"></a>
  <a href="docs/contributing.md"><img alt="Contributions welcome" src="https://img.shields.io/badge/Contributions-welcome-22c55e?style=flat&logo=github&logoColor=white"></a>
</p>

<p align="center">
  <a href="#showcase">Showcase</a> |
  <a href="#milestones">Milestones</a> |
  <a href="#quick-start">Quick Start</a> |
  <a href="#how-it-works">How It Works</a> |
  <a href="#star-history">Star History</a> |
  <a href="docs/method.md">Method</a> |
  <a href="docs/contributing.md">Contribute</a>
</p>

<p align="center">
  <b>English</b> | <a href="README.zh-CN.md">中文</a>
</p>

<h2 id="milestones">🔥 Milestones</h2>

- **Algorithm update:** the Codex path now uses role-separated Drawer / Reviewer agents, far-near visual review views, reviewer bounding boxes, and annotated feedback passed into the next iteration.
- **Evaluation update:** we have built an internal hybrid style scorer for repeatable method comparison. A paper release covering the scorer protocol, benchmark design, and analysis is planned.

<p align="center">
  <video src="https://github.com/user-attachments/assets/0656009c-77c7-41e5-8423-07c3411aef13" width="900" controls
  muted playsinline></video>
</p>

<h2 id="showcase"><img src="docs/assets/icons/showcase.svg" alt="" width="22" height="22" align="absmiddle"> Showcase</h2>

FigMirror uses a reference figure as the style target, then renders your data through an iterative Drawer / Reviewer loop until the output looks like it belongs in the same paper family.

<table>
<tr>
<td width="50%" align="center"><b>Reference</b></td>
<td width="50%" align="center"><b>FigMirror Output</b></td>
</tr>
<tr>
<td><img src="docs/assets/showcase/primary-reference.jpg" alt="reference paper-style figure" width="100%"/></td>
<td><img src="docs/assets/showcase/primary-generated.jpg" alt="FigMirror generated paper-style output" width="100%"/></td>
</tr>
</table>

<table>
<tr>
<td width="25%" align="center"><b>Reference</b></td>
<td width="25%" align="center"><b>FigMirror Output</b></td>
<td width="25%" align="center"><b>Reference</b></td>
<td width="25%" align="center"><b>FigMirror Output</b></td>
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
  <sub>No high-quality reference at hand? Start from 139 paper figures across 25 chart families.</sub>
</p>

<h2 id="quick-start"><img src="docs/assets/icons/quick-start.svg" alt="" width="22" height="22" align="absmiddle"> Quick Start</h2>

<h3 id="install-with-your-agent"><img src="docs/assets/icons/agent.svg" alt="" width="18" height="18" align="absmiddle"> Install With Your Claude / Codex</h3>

Already inside Claude Code or Codex? Paste this and let the agent do the setup:

```text
Install FigMirror for me: https://github.com/VILA-Lab/FigMirror
```

<h3 id="web-ui"><img src="docs/assets/icons/web-ui.svg" alt="" width="18" height="18" align="absmiddle"> Web UI</h3>

Use this when you want upload, preview, iteration history, and refinement in a browser.

If `uv` is missing: `python3 -m pip install uv`.

```bash
git clone https://github.com/VILA-Lab/FigMirror.git && cd FigMirror
bash scripts/install.sh
uv run python scripts/figcopy_serve.py --workspace .artifacts/figmirror-workspace --backend codex
```

Open `http://127.0.0.1:8765/`.

The installer auto-detects Claude Code and Codex. For Codex, it installs the
`figmirror` skill plus the `figmirror-drawer` and `figmirror-reviewer` custom
agents used by the current role-separated algorithm.

<a id="codex-skill"></a>
<a id="claude-code-skill"></a>

<h3 id="skill-only"><img src="docs/assets/icons/skill.svg" alt="" width="18" height="18" align="absmiddle"> Skill Only</h3>

Use this when you want FigMirror inside your agent, no web UI.

```bash
curl -fsSL https://raw.githubusercontent.com/VILA-Lab/FigMirror/main/scripts/install.sh | bash
```

Target one runtime explicitly:

```bash
curl -fsSL https://raw.githubusercontent.com/VILA-Lab/FigMirror/main/scripts/install.sh | bash -s -- --codex
curl -fsSL https://raw.githubusercontent.com/VILA-Lab/FigMirror/main/scripts/install.sh | bash -s -- --claude
curl -fsSL https://raw.githubusercontent.com/VILA-Lab/FigMirror/main/scripts/install.sh | bash -s -- --all
```

Then attach a paper-figure screenshot, paste your data, and ask:

```text
Use FigMirror to mirror this figure's style with my data.
```

Need manual target selection, Claude backend, or troubleshooting? See [Detailed Install](docs/install.md).

<h2 id="how-it-works"><img src="docs/assets/icons/how-it-works.svg" alt="" width="22" height="22" align="absmiddle"> How It Works</h2>

<p align="center">
  <img src="docs/assets/algorithm.png" alt="FigMirror architecture loop and grounded measurement algorithm" width="860"/>
</p>

> Illustration of FigMirror. The left panel shows the core agentic loop; the right panel introduces Grounded Measurement.

FigMirror uses an agentic Drawer-Reviewer loop. In the current Codex path, a top-level Orchestrator delegates drawing to a named Drawer agent and visual audit to a named Reviewer agent. The Reviewer sees a far-view composite plus full-resolution source and candidate views, returns structured feedback with bounding boxes, and the Orchestrator turns that feedback into an annotated image and notes for the next Drawer pass.

The Drawer renders a candidate figure with ***Grounded Measurement***. The Reviewer compares it with the reference image, then returns a visual review, a revision checklist, and a preserve list. The preserve list accumulates across iterations as an anchor against style drift. The Aesthetic Lib provides fallback principles, style rules, and figure properties when the agents disagree or the Drawer has low confidence.

For 3D figures, FigMirror adds geometry-aware prompting for camera, scale, surfaces, lighting, and repair checks, helping the loop preserve the 3D composition of the reference figure while producing editable matplotlib code.

***Grounded Measurement*** builds on two properties of computer-use-trained foundation models: *Measurement with Axis*, which lets the model return x/y coordinates for visual targets; and *Resonate with Code*, which turns those coordinates into executable checks, such as cropping a line segment and reading its color from pixels.

For the detailed algorithm, architecture, product envelope, and spec map, read [docs/method.md](docs/method.md). For the web UI, see [scripts/README_figcopy_serve.md](scripts/README_figcopy_serve.md).

<h2 id="contributing"><img src="docs/assets/icons/contributing.svg" alt="" width="22" height="22" align="absmiddle"> Contributing</h2>

FigMirror welcomes contributions!

Open an issue for bugs, broken installs, or figure cases FigMirror should learn from; open a PR for showcase examples, prompt improvements, UI polish, or small regression tests.

- **Showcase cases:** add reference/output pairs that prove the method across chart families.
- **UI polish:** make the web workflow feel instant, legible, and forgiving.
- **Prompt and reviewer quality:** improve the Drawer / Reviewer loop without weakening the grounding rules.
- **Evaluation:** add reproducible cases that catch visual drift, floor violations, and broken exports.

Start with [docs/contributing.md](docs/contributing.md). Good first PRs include adding a showcase example, improving a web UI interaction, tightening install docs, or adding a small regression test around runner behavior.

<h2 id="star-history"><img src="docs/assets/icons/star-history.svg" alt="" width="22" height="22" align="absmiddle"> Star History</h2>

<p align="center">
  <a href="https://star-history.com/#vila-lab/figmirror&Date">
    <img alt="FigMirror GitHub star history chart" src="https://api.star-history.com/svg?repos=vila-lab/figmirror&type=Date" width="100%">
  </a>
</p>

<p align="center">
  <sub>Rendered by Star History from GitHub stargazer data, so the chart stays current as the repository grows.</sub>
</p>

<h2 id="roadmap"><img src="docs/assets/icons/roadmap.svg" alt="" width="22" height="22" align="absmiddle"> Roadmap</h2>

- [x] Ship the reference-to-figure loop as Codex and Claude Code skills.
- [x] Add a local Web UI for upload, iteration browsing, and refinement.
- [x] Publish a 139-figure gallery so users can start without hunting for references.
- [x] Refine the Codex algorithm with role-separated Drawer / Reviewer agents and bbox-annotated visual feedback.
- [x] Build an internal hybrid style scorer for repeatable method comparison.
- [ ] Release the scorer protocol, benchmark design, baselines, and analysis in a paper.
- [ ] Curate a public benchmark set with reference figures, input data, generated outputs, and human preference labels.
- [ ] Harden install and runtime docs for more OpenAI-compatible and local-agent backends.
