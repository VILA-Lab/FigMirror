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
| `.codex/skills/figmirror/references/three-d-prompting.md` + `references/three-d/*.md` | Optional 3D insert, loaded only by the 3D gate |
| `.claude/skills/figmirror/SKILL.md` + `.claude/agents/figure-{preprocessor,illustrator,critic}.md` | Claude runtime skill + subagent prompt files |
| `resources/prompts/figure-style-copier.md` | Development/historical consolidated Claude prompt bundle; release/runtime routes through the skill files |
| `resources/prompts/figure-style-copier-codex.md` | Development/historical consolidated Codex prompt bundle; release/runtime routes through `.codex/skills/.../references/*.md` |
| `openspec/changes/phase0-style-transfer-loop/` | Design decisions (design.md), task tracker (tasks.md), proposal (proposal.md) |
| `openspec/sessions/` | Design archive |

## How to run FigMirror in Codex

1. Read `.codex/skills/figmirror/SKILL.md` — it tells you how the
   top-level Codex process orchestrates the Drawer/Reviewer loop.
2. Treat `.codex/skills/figmirror/references/*.md` as the Codex
   runtime prompt surface. Update these files first when changing behavior.
3. The reference preprocessor and Reviewer subprocesses are launched via:
   ```bash
   CODEX=/Applications/Codex.app/Contents/Resources/codex
   ```
   Verify this path exists before starting.

## Key architectural facts

- **No runtime Python library.** The core system is prompts + `codex exec`
  Bash tooling. `pyproject.toml` exists for project metadata and dev deps.
- **Codex Orchestrator + Drawer run in-process.** The main Codex session owns
  loop state, drawing, floor self-checks, and final selection. Reference
  preprocessing and Reviewer audit remain separate bounded `codex exec` passes.
  Native subagents are experimental only and should not be used in the default
  FigMirror path.
- **Reviewer tool restriction is prompt-level in Codex** (no `--allowedTools`
  equivalent). The Reviewer prompt instructs it not to write files; track violations.
- **Aesthetic library and gated inserts are the living layers.** Keep runtime
  behavior in the structured skill files, not in consolidated prompt bundles.

## Current status (as of 2026-05-06)

FigMirror ships as structured Claude Code and Codex skills. Pending product work
is tracked in `openspec/changes/phase0-style-transfer-loop/tasks.md`.
