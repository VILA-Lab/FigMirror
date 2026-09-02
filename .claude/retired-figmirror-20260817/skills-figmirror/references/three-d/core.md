# 3D Core Rules

Use for every routed 3D run.

## Inventory

Before drawing, record the visible subtype, camera class, box/subject occupancy,
topology/footprint, colorbar or legend ownership, and export risks. Use only
visible evidence; do not infer hidden formulas or coordinates.

## Camera And Footprint

Treat camera, box aspect, limits, subplot rectangle, and crop as visible design
variables. Choose them from final-image cues: floor shape, z-wall height, front
edge length, visible z-axis side, subject fill, and label footprint.

Change global register only when that record is wrong. Otherwise keep register
stable and repair local 3D cues such as depth order, mark density, mesh/facets,
alpha layering, ridge visibility, or relief rank. A cleaner view is not better
if it shrinks, flattens, over-zooms, clips, or changes the chart family.

## Composition And Export

Judge only the final PNG/PDF scale. Preserve plot-box placement, subject fill,
whitespace, title/legend/colorbar anchors, and typography scale. Text/export is
a hard floor: clipped or colliding titles, ticks, z-labels, legends, colorbars,
or annotations fail even when geometry improves.

## Color And Panes

Preserve visible color semantics: sequential, diverging, categorical, or
material lighting. Pane/grid/axis lines stay recessive; projections or contour
shadows must read as guides rather than new data.

## Ship Gate

Before selection, compare against the conservative render under identical export
settings. Ship only when visible 3D class, topology/footprint, camera/aspect,
occupancy, color/legend treatment, and export readability are all mode-consistent.
