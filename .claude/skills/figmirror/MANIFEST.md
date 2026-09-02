# MANIFEST — Claude-side FigMirror bundle provenance

Initially sourced from `.codex/skills/figmirror` in this repo at commit
`1f2478acdb5e132711c0a0cf5298102505f5c9ef` and copied on 2026-08-17.
Release-integration fixes are applied symmetrically to both bundles; the hashes
below are authoritative for the current files. `__pycache__` is excluded.

## Ported byte-for-byte

Identical to their Codex counterparts. Verify with:

```
diff -r --exclude=__pycache__ \
  --exclude=MANIFEST.md --exclude=SKILL.md \
  --exclude=orchestrator-claude.md --exclude=fit_images.py \
  --exclude=agents \
  .codex/skills/figmirror .claude/skills/figmirror
```

It must print nothing.

| sha256 | file |
|---|---|
| `e464be689dad19e19a0b6eca58bfc5d1b30bdcb2eac380f6186c06b5192e2c0d` | `references/aesthetic-library.md` |
| `e1c94a3f69c54333bd4deacd92ad2b50e0743b4f345b1e2bd069067aa6542c97` | `references/drawer.md` |
| `e79dc93817da8bbe901cfd80adbfbb131e22d2693117c3f71a15e14c53cd3729` | `references/orchestrator-codex.md` |
| `d6ffb28ec80270531d37db2dda7bc82d81656cf0dbe296ae9847c07c82291cde` | `references/preprocessor.md` |
| `d85eb37dd3765ed8edd5c28e5a1694c75c3219b7de54b54615cef031220d81e9` | `references/reviewer.md` |
| `3e25ed3fbf490d389b62b22b8ef2a3ddce2adaf78ac56aa9f6e350788bddcd4c` | `references/three-d/candidate-selection.md` |
| `8ce47378537fcade3148456e7c131ca655ab20a058811e78d178cf08b7440923` | `references/three-d/core.md` |
| `7d30e282607643dca9801e44cfed4c3e20bf0ffdb67427a80d2c2d8d2a1e428d` | `references/three-d/extrema-fold-network.md` |
| `9ee241c5d6ce01db2fdefb5219229e18dd6110c72cc53a77aaec3b2bb4b01ba6` | `references/three-d/fractured-surfaces.md` |
| `55d64622feb65d5d2c887e5ae49714570c886d518c0a3c17a1dd6e02ddaa0e72` | `references/three-d/marks-and-panels.md` |
| `83812855c73dedba30982605d6df45639321fa7354bc7f67e81572a95f0b028f` | `references/three-d/material-lighting.md` |
| `8c7a902b7e0a26c6d1474ab802407068dee3c01f7e8af0c3a07e1119dcfd764a` | `references/three-d/patch-composition.md` |
| `3d26bd3440cabc8d19befb19af6fa9d540b2ca065f430f73a2747ecf083fb84e` | `references/three-d/repair-feedback.md` |
| `468783716331d6d60e7c5eabce921a8b5f164a20987c8735bdd33362c3c24406` | `references/three-d/reviewer-scorecard.md` |
| `2a9ac9b5f6c15e4ddabe193e74198098966043a7fb17b9f1531d887cc29be91d` | `references/three-d/scale-occupancy.md` |
| `ee124c42df7bac2fecdb87ea064df71e1fede1e0d6f9d81d89ad9708995daaec` | `references/three-d/strict-reproduction.md` |
| `96f2e3d3cba7a3bd12df8009788dbcdbed6d4c04852fa21818074b1e4c6e4858` | `references/three-d/style-transfer.md` |
| `863faeaabacb531b6758ac5e2ccd3e3534fc72c20da56e8d9c0af17f610f0da5` | `references/three-d/surfaces.md` |
| `8058c83f8e6453e39ed0e08c62f45288eb1b9d8c57733e2676d53e45f162fd21` | `references/three-d/volumetric-surfaces.md` |
| `2f402f63996f3eb4f477c308185bf16fa43323b424d60eb84f6783dea2c36239` | `references/three-d-prompting.md` |
| `0016e2668d113975a02663f5606525575a67256bd82a2a4d0a0ff21577042175` | `scripts/figannot.py` |
| `2c96431c71edbc458107f9fcdb2b848d25dc58334512dfb214abd9a2ce77c31d` | `scripts/score_3d_candidates.py` |

`references/orchestrator-codex.md` is in that list on purpose. It is not
followed at runtime; it is the diff baseline that makes every change in
`orchestrator-claude.md` reviewable.

## Not carried across

`agents/openai.yaml` — the Codex UI manifest (`display_name`,
`short_description`, `default_prompt`). It configures how the skill is
presented in the Codex client and nothing here reads it, so carrying it
would be dead weight that implies a Claude-side agents layer exists.

## Adapted for this harness

`SKILL.md` is the entry point Claude actually loads, so it cannot stay
byte-identical: the Codex version names `orchestrator-codex.md`,
`spawn_agent`, `fork_context`, and the `items` image-attachment channel,
none of which exist here. Changed lines, and nothing else:

- "main Codex process" / "top-level Codex process" -> "main `claude` process"
- Drawer and Reviewer dispatch: `spawn_agent` + `fork_context=false` ->
  `Task` with `subagent_type` + `run_in_background=false`
- Reviewer image delivery: one structured `items` payload of attached pixels
  plus a no-`view_image` rule -> an ordered list of absolute paths the
  Reviewer opens with `Read`, once each
- Loop-wiring reference and staged prompt filename: `orchestrator-codex.md` ->
  `orchestrator-claude.md`
- Step 6 additionally names `scripts/fit_images.py`; the artifact layout adds
  `tools/fit_images.py` and `image_fit_<N>.json`

The port preserves the shared budget and artifact contract. Release integration
adds a dependency-aware Python fallback for direct skill invocation and makes
the deterministic gate enforce the required Reviewer schema and existing
strict-3D score thresholds. Step 2 also names how the Stage-0 preprocessor is
dispatched here, and step 6 names `fit_images.py`.

## Added for this harness

The first two live outside the skill directory, so the diff recipe above does
not reach them. Verify their bodies against the Codex role files directly.


| sha256 | file | why |
|---|---|---|
| `a6b609ee49e57045f27522d5448b12b58cb5e0c70c33ec0471739c121291b150` | `.claude/agents/figmirror-drawer.md` | Role definition. Body is the `developer_instructions` string of `.codex/agents/figmirror-drawer.toml`, verbatim; the YAML frontmatter is new. |
| `cd21dd63bb85f4e1b199fc9f71cea35e2310d0974f807909e8658a4f9b9c4006` | `.claude/agents/figmirror-reviewer.md` | Role definition. Body is the `developer_instructions` string of `.codex/agents/figmirror-reviewer.toml`, verbatim; the YAML frontmatter is new. |
| `46896f3e05c8f4e74c34c9dbceeb3c85582269a64581991f8e654d4d0a6f2b1c` | `references/orchestrator-claude.md` | Dispatch-mechanism port of orchestrator-codex.md. Algorithm, decision state machine, iteration budget and fail-closed rules remain shared. |
| `fae45be60ded67b7b619a77c0f7774157022f873e8eed4b6ab53a74b8f52ad93` | `scripts/fit_images.py` | Fits staged audit-view images to the 2000px delivery limit so the delivered pixels are a recorded property of the run. Refuses any path outside audit_view_<N>/, and composite.png inside it. |
