# 3D Marks, Layers, And Panels

Read for scatter, trajectories, bars, histograms, waterfalls/ribbons, projection
planes, small multiples, and mixed 2D/3D figures.

## Marks

Scatter preserves density class, hulls, overlap/separation, outliers, marker
size, alpha, depth order, edges, and legend ownership. Style transfer maps this
grammar to user data; strict reproduction also preserves visible category layout
without inferring hidden coordinates.

Trajectories preserve endpoints, direction, crossings, hubs, envelope, depth
order, and draw order. Do not rotate camera to hide a wrong path skeleton.

Bars/histograms preserve floor footprint, height register, depth order, category
spacing, width-to-gap class, and labels. Do not fix labels by shrinking bars or
merging bars into walls/ribbons.

## Layers And Projections

Waterfalls/ribbons/curtains preserve layer family, spacing, orientation,
front/back order, alpha, endpoints, height rank, and projection anchoring. Plane
projections and floor maps are low-alpha guides pinned to their plane, not
primary marks.

## Panels

Small multiples preserve panel count/order in strict mode; style transfer uses
the user's panel structure while preserving shared camera, label hierarchy,
spacing, and per-panel occupancy.

For mixed 2D/3D figures, lock panel count, order, type, relative size,
legend/colorbar ownership, read path, and title hierarchy before 3D repair. The
3D insert must not drop, crop, shrink, reorder, or restyle companion panels.

When space is tight, adjust canvas, subplot ratios, margins, or colorbar padding
with mark footprint and camera fixed.
