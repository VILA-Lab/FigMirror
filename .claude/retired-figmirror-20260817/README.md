# Retired Claude-side FigMirror implementation (archived 2026-08-17)

This is the **older, independently-evolved** Claude-side implementation. It is
kept for reference only and must not be used as a source of truth.

It is not the same lineage as the Codex bundle under `.codex/skills/figmirror`.
Concrete divergences found during the 2026-08 port:

- `references/iter-loop-spec.md:107` defaults `max_iters` to 6; the production
  Codex config uses 5 (and 3 for the subscription lane).
- Its 3D staging writes only to `inputs/three-d`, while the Codex orchestrator
  writes to both `prompts/` and `inputs/` (`orchestrator-codex.md:80-84`).
- It has no `figannot.py`: the ship/draw decision is made inline by the
  orchestrator rather than by a deterministic state machine.
- `scripts/install_claude_skill.py` asserted that Claude Code subagents cannot
  spawn subagents (citing Anthropic issue #4182) and built its whole
  no-orchestrator-subagent design on that premise. Measured on Claude Code
  2.1.232, a three-level spawn works and the default depth limit is 3, so the
  premise is false.

The replacement is a faithful port of the Codex bundle, at
`.claude/skills/figmirror`.
