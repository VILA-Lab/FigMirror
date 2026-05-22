# 3D Volumetric Surfaces

Read with `surfaces.md` when L1 shows compact mass, side faces, cliffs, exposed
thickness, basin/cavity/funnel, self-occluding folds, layered relief, shell
pieces, or dense ridges that read as object rather than low terrain.

A single-valued `z=f(x,y)` is acceptable only for terrain-like L1. Exit
heightfields when they cannot express walls, folds, self-overlap, cavity,
side-thickness, compact mass, or broken boundary. Colormap, noise, z-scale, and
camera angle do not count as exits.

Use visible components before styling: irregular envelope, foreground/basin/rear
and side masses, rim-to-basin ridges, upward/downward extrema, partial wall or
fold faces, serrated relief, then camera/box preserving depth and occupancy.
Useful primitives include multiple surfaces, irregular triangulation,
`Poly3DCollection` faces, stacked/intersecting patches, shell pieces, and
cavity-wall pieces.

Volumetric construction must not become a box, tray, plinth, side-band, or
continuous wall unless L1 shows it. Side patches are partial, height-varying,
and tied to the silhouette/fold network.

Reject shallow-terrain substitution, decorative side skins, enclosure/support
artifacts, one spike/pit replacing distributed structure, missing underside,
random triangulation, cloth-like smoothing, or small/airy top-skin output. The
next repair names the missing mass, fold, side, cavity, or relief component.
