# Contributing to FigMirror

Thanks for helping make research figures less painful. FigMirror is easiest to improve when contributions stay close to a visible user workflow: upload a reference, paste data, inspect iterations, export a polished figure.

## Setup

```bash
git clone https://github.com/VILA-Lab/FigMirror.git
cd FigMirror

mkdir -p .artifacts/uv-cache
export UV_CACHE_DIR="$PWD/.artifacts/uv-cache"
uv sync --group dev
uv run pytest
```

For UI work, run the mock backend first:

```bash
mkdir -p .artifacts/figmirror-dev
uv run python scripts/figcopy_serve.py \
  --workspace .artifacts/figmirror-dev \
  --backend mock
```

Open `http://127.0.0.1:8765/`.

## Good First PRs

- Add a showcase reference/output pair under `docs/assets/showcase/` and update the README gallery.
- Improve a small web UI interaction in `scripts/figcopy_static/`.
- Add a regression test for runner state, chat logs, backend availability, or export behavior under `tests/`.
- Clarify an install or usage path in README or `scripts/README_figcopy_serve.md`.
- Add a compact eval case to a skill bundle when a visual failure repeats.

## Project Areas

### Web UI

Primary files:

- `scripts/figcopy_serve.py`
- `scripts/figcopy_static/workspace.js`
- `scripts/figcopy_static/trajectory.js`
- `scripts/figcopy_static/style.css`
- `scripts/figcopy_runner/`

Use `--backend mock` for fast UI iteration. Use `--backend codex` or `--backend claude` when testing real generation.

### Skills and Prompts

Primary files:

- `.codex/skills/figmirror/`
- `.claude/skills/figmirror/`
- `.codex/agents/figmirror-{drawer,reviewer}.toml`
- `.claude/agents/figmirror-{drawer,reviewer}.md`

Development/history bundles may live under `resources/prompts/`, but prompt
changes intended for runtime should land in the Codex/Claude skill files above.

Prompt changes should keep the L1/L2 grounding contract intact: visual decisions come from the reference image or aesthetic library, and the reviewer preserve list should survive into the next iteration. Both harnesses use a hard `max_iters` Drawer cap.

### Tests

Run the suite before opening a PR:

```bash
uv run pytest
```

Focused tests live under `tests/figcopy_runner/`. Add tests near the behavior you changed.

### Showcase Assets

Use small, committed assets for README examples. Keep heavy run workspaces under `.artifacts/` or a project-specific external volume, and avoid committing raw iteration folders, caches, or large datasets.

Recommended naming:

```text
docs/assets/showcase/<short-case-name>-reference.png
docs/assets/showcase/<short-case-name>-generated.png
```

If you add a new image pair, include a short note in the PR explaining the chart family, source paper or reference, and what style property the example proves.

## PR Checklist

- The README still opens with the title, demo slot, showcase, and Quick Start.
- New public-facing docs are short enough to scan.
- UI changes were checked in a browser with the mock backend.
- Prompt changes were tested on at least one concrete reference/data pair.
- `uv run pytest` passes, or the PR explains the remaining failure.
