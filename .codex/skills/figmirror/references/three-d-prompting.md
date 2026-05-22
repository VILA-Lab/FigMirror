# 3D Prompting Router

Load this file only when the user asks for 3D, the reference is visibly 3D, or
the data needs 3D encodings. Do not turn ordinary 2D figures into 3D.

Read exactly one mode:

- `three-d/style-transfer.md`: default for user-data figures. Transfer the
  reference's visible 3D grammar while user data owns values, counts, categories,
  coordinates, and labels.
- `three-d/strict-reproduction.md`: only for reproduction, candidate comparison,
  quantitative scoring, or control replacement. The reference owns visible 3D
  layout at category level.

Both modes read `three-d/core.md` and `three-d/scale-occupancy.md`.

Read subtype modules only when their trigger is visible:

- `three-d/surfaces.md`: surfaces, terrains, meshes, folded forms, or objects.
- `three-d/fractured-surfaces.md`: craggy, faceted, broken, or sharply folded
  material.
- `three-d/material-lighting.md`: color is lighting/material rather than a data
  colormap.
- `three-d/volumetric-surfaces.md`: compact mass, cavity, side faces, shell,
  self-overlap, or occluding folds.
- `three-d/extrema-fold-network.md`: peaks, troughs, basins, rims, cliffs, or
  folds define the skeleton.
- `three-d/patch-composition.md`: one smooth sheet would collapse separate
  visible roles.
- `three-d/marks-and-panels.md`: scatter, trajectories, bars, waterfalls,
  projection planes, small multiples, or mixed 2D/3D figures.

Strict mode also reads `three-d/candidate-selection.md`,
`three-d/reviewer-scorecard.md`, and `three-d/repair-feedback.md`.

If a routed module is missing, fail closed; do not silently fall back to 2D.
