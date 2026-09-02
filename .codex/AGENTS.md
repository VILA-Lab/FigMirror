# FigMirror — Codex project context

This repo contains **FigMirror**, shipped through the
`figmirror` Claude Code / Codex skill id. It transfers the visual
style of a reference paper figure onto the user's own data, producing a
camera-ready PDF and a self-contained matplotlib script.

## What lives here

| Path | What it is |
|------|------------|
| `.codex/skills/figmirror/SKILL.md` | Codex runtime entry point — read this first when the user invokes FigMirror / `figmirror` |
| `.codex/skills/figmirror/references/*.md` | Codex runtime prompt files: Preprocessor, Drawer, Reviewer, Orchestrator, and Aesthetic library |
| `.codex/agents/figmirror-{drawer,reviewer}.toml` | Codex custom-agent role boundaries used by the role-separated algorithm |
| `.codex/skills/figmirror/references/three-d-prompting.md` + `references/three-d/*.md` | Optional 3D insert, loaded only by the 3D gate |
| `.claude/skills/figmirror/SKILL.md` + `.claude/agents/figmirror-{drawer,reviewer}.md` | Claude runtime skill + named loop roles; Stage 0 uses the bundled preprocessor prompt as a bounded general task |
| `scripts/figcopy_runner/{codex,claude}.py` | Web UI adapters that launch the installed skills and inject the repository Python command |
| `resources/prompts/figure-style-copier.md` | Development/historical consolidated Claude prompt bundle; release/runtime routes through the skill files |
| `resources/prompts/figure-style-copier-codex.md` | Development/historical consolidated Codex prompt bundle; release/runtime routes through `.codex/skills/.../references/*.md` |
| `openspec/changes/phase0-style-transfer-loop/` | Design decisions (design.md), task tracker (tasks.md), proposal (proposal.md) |
| `openspec/sessions/` | Design archive |

## How to run FigMirror in Codex

1. Read `.codex/skills/figmirror/SKILL.md` — it tells you how the
   top-level Codex process orchestrates the Drawer/Reviewer loop.
2. Before changing FigMirror skill prompts, role transport, or the
   Orchestrator loop, read `.codex/agents/figmirror-drawer.toml`,
   `.codex/agents/figmirror-reviewer.toml`, and `docs/method.md` so the
   named-role gate and annotated feedback chain stay aligned.
3. Treat `.codex/skills/figmirror/references/*.md` as the Codex
   runtime prompt surface. Update these files first when changing behavior.
4. The default Codex loop requires named project custom agents
   `figmirror-drawer` and `figmirror-reviewer`. The Experiment adapter installs
   those role configs into its per-run `CODEX_HOME` before launching Codex.

## Key architectural facts

- **No runtime Python library.** The core system is prompts plus Codex role
  transport. `pyproject.toml` exists for project metadata and dev deps.
- **Codex Orchestrator is role-separated.** The main Codex session owns loop
  state, role dispatch, artifact checks, and final selection. Drawing is handled
  by `figmirror-drawer`; visual audit is handled by `figmirror-reviewer`.
- **Reviewer tool restriction is prompt-level in Codex** (no `--allowedTools`
  equivalent). The Reviewer prompt instructs it not to write files; track violations.
- **Aesthetic library and gated inserts are the living layers.** Keep runtime
  behavior in the structured skill files, not in consolidated prompt bundles.

## Current status (as of 2026-09-02)

FigMirror ships as structured Claude Code and Codex skills. The Codex path uses
named Drawer/Reviewer roles plus deterministic annotation tooling for bbox-based
review feedback. The Claude path uses the same role split and decision state
machine through Claude Code's synchronous `Task` transport.
