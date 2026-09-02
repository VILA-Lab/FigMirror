# Detailed Install

Most users should start from the README Quick Start. This page is for manual target selection, backend changes, and setup troubleshooting.

## Install With Your Agent

Already inside Claude Code or Codex? Paste this into the agent:

```text
Install FigMirror for me: https://github.com/VILA-Lab/FigMirror
```

## Skill Install

The skill's bundled Python helpers use `uv`. Install it first with
`python3 -m pip install uv` if needed. Web UI runners inject a project-pinned
Python command; direct skill-only runs use an isolated `uv run --with ...`
fallback. On shared or small-root machines, run `df -h` first and export
`UV_CACHE_DIR` to a user-owned directory on the largest non-root writable
filesystem.

Auto-detect Codex and Claude Code:

```bash
curl -fsSL https://raw.githubusercontent.com/VILA-Lab/FigMirror/main/scripts/install.sh | bash
```

For both Codex and Claude Code, this installs the `figmirror` skill and the
required custom agents: `figmirror-drawer` and `figmirror-reviewer`. Stage 0
reference cleanup is bundled in the skill and runs as a bounded general task.

Choose a target explicitly:

```bash
curl -fsSL https://raw.githubusercontent.com/VILA-Lab/FigMirror/main/scripts/install.sh | bash -s -- --codex
curl -fsSL https://raw.githubusercontent.com/VILA-Lab/FigMirror/main/scripts/install.sh | bash -s -- --claude
curl -fsSL https://raw.githubusercontent.com/VILA-Lab/FigMirror/main/scripts/install.sh | bash -s -- --all
```

The installed skill id is `figmirror`. Claude Code honors
`CLAUDE_CONFIG_DIR`; `CLAUDE_HOME` remains a compatibility alias for the
installers in this repository.

## Web UI

Install `uv` if needed:

```bash
python3 -m pip install uv
```

Start the UI with Codex:

```bash
git clone https://github.com/VILA-Lab/FigMirror.git && cd FigMirror
bash scripts/install.sh
uv run python scripts/figcopy_serve.py --workspace .artifacts/figmirror-workspace --backend codex
```

Open `http://127.0.0.1:8765/`.

Claude Code users can replace `--backend codex` with `--backend claude`.

Optional: set `UV_CACHE_DIR=.artifacts/uv-cache` before `uv run` if you want uv's package cache to stay inside the project instead of your home cache.

## Local Clone Installers

If you already cloned the repo and want installer validation flags:

```bash
uv run python scripts/install_codex_skill.py --dry-run
uv run python scripts/install_claude_skill.py --dry-run
```

Both local installers support `--dry-run`, `--validate-only`, and `--target`.
