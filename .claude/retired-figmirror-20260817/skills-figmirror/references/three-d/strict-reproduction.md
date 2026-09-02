# 3D Strict Reproduction Mode

Use only for reproduction, candidate comparison, quantitative scoring, or final
control replacement. Read `three-d/candidate-selection.md`,
`three-d/reviewer-scorecard.md`, and `three-d/repair-feedback.md`. Stage
`scripts/score_3d_candidates.py` only when quantitative raster diagnosis is
explicitly enabled.

## Non-Compensatory Records

These records cannot trade off: representation/topology, projected footprint,
camera/box aspect, composition/occupancy, colorbar/legend/text floor, surface or
mark style, and color semantics. Palette, lighting, or cleaner labels cannot
compensate for wrong chart class, broad-sheet substitution, missing panels,
clipped labels, or regressed camera/occupancy.

## Drawer Contract

Before first render, record the visible inventory and make a conservative L1
candidate. Later repairs start from the accepted rendered control and move one
active visible record, unless the representation class itself is wrong. Any
independent redraw is only a probe until rebuilt under accepted camera, limits,
crop, scale, palette/material, text/export, and mark locks.

For risky repairs, note active dimension, preserved locks, 2-4 visible landmarks,
movement direction, and reject-if tests. If locks cannot hold, output the
current control and mark the dimension unresolved.

## Review And Selection

The Reviewer compares rendered images only and must provide `three_d_scorecard`,
movement delta, preserve list, regressions, remaining gaps, and one next repair.
Invalid JSON, missing scorecards, failed hard gates, or primary regressions block
strict final selection.

Rendered outputs only can win; the last iteration does not automatically win.
Separate "better than first baseline" from "accepted over current control".
