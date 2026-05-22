# 3D Surface Rules

Read for surfaces, meshes, terrains, folded/faceted forms, and object-like 3D
subjects.

## Classify First

Record rendering style, envelope, and skeleton:

- style: smooth, banded, faceted, triangulated, meshed, matte/glossy, wire-edged.
- envelope: broad sheet, clipped patch, compact mass, folded/cavity form,
  side-walled form, closed or near-closed object.
- skeleton: peaks, troughs, saddles, basins, ridges, folds, side faces,
  foreground relief, and silhouette.

Do not swap representation families because a primitive is easier. A surface is
not a scatter; a compact/folded/cavity form is not a rectangular heightfield; a
faceted mesh is not a smooth surface.

## Construction

For smooth/rippled surfaces, match large extrema and topology before formula
polish. For gridded or triangulated surfaces, cell/triangle density, edge
treatment, and ridge alignment are part of geometry. For fractured or folded
surfaces, smooth heightfield substitution is a topology failure.

Compact, folded, cavity-like, side-walled, or object-like references need
clipped domains, partial faces, folded layers, basin/ridge skeletons, or
occluding relief. Z-scale on one global terrain does not substitute for visible
mass, walls, cavities, or folds.

Side/fold faces must be partial, height-varying, and tied to relief unless L1
shows a continuous wall. Reject tray, fence, moat, plinth, support, or enclosing
frame artifacts when L1 has open organic edges.

## Floors

Keep surface repair active when the candidate becomes a wide sheet, tray/frame,
enclosing wall, smooth slab, ribbon/island, long flat slab, low terrain, or top
skin that loses compact mass, cavity depth, side faces, ridges, or vertical
presence. The next repair names the missing construction, not color or crop.
