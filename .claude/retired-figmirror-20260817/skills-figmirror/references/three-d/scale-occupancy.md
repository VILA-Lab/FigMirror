# 3D Scale And Occupancy

Read for every 3D run.

The final PNG/PDF is the only scale of record. Judge camera, box aspect, subject
size, labels, margins, colorbars, and whitespace under final `figsize`, DPI,
limits, view, `bbox_inches`, and `pad_inches`. Diagnostic views do not count
unless rerendered at final settings.

Track two records:

- plot-box occupancy: where the 3D axes box sits in the canvas.
- subject occupancy: how much of that box/canvas the object or marks fill.

Strict reproduction preserves both at L1 category level. Style transfer
preserves readable category register and density while user data may change
ranges, counts, and footprint.

Reject a repair that improves local detail while the final subject becomes
smaller, flatter, airier, overfilled, clipped, distant, label-dependent, or less
vertically/depth-wise present. For scale-active repairs, change one registration
family at a time: view, limits/footprint, box aspect, subplot/crop, or z-relief.

When scoring composition/occupancy, include final-export subject fill and 3D box
fill, not just centering. Keep an earlier render if a later one has better
texture but worse occupancy.
