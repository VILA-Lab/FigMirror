# 3D Fractured Surfaces

Read with `surfaces.md` when L1 shows facets, crisp creases, jagged ridges,
cracks, crags, serrated boundaries, clustered sharp peaks, abrupt normals, or
material lighting. Do not use for smooth mathematical surfaces with incidental
texture.

A fractured surface needs three visible scales:

1. large envelope: footprint, basin, wall, ridge field, or high/low mass.
2. mid-scale crease graph: connected ridges, valleys, rims, seams, fold axes.
3. local facets: plates, small triangles, broken highlights, cracks, serrations.

Build in that order. Random noise, uniform low-poly styling, or global roughness
is not fracture. Local detail must attach to the accepted envelope and crease
graph.

Useful constructions include irregular triangulation, piecewise planar plates,
partial `Poly3DCollection` faces, ridge strips, local normal changes, and crag
clusters along folds or broken edges. Control artifacts: contours, mesh outlines,
hanging triangles, clipping fringes, or edge clutter fail unless L1 shows them.

Reject detail that erodes footprint, smooths ridges into cloth, creates random
spikes, drifts into ribbon/tray/plinth forms, changes material class, or hides
problems through zoom/crop/z-flattening. Style score inspects crease sharpness,
plate edges, boundary serration, shadow contrast, and whether facets follow the
larger fold network.
