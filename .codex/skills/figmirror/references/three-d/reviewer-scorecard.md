# 3D Reviewer Scorecard

Read for strict reproduction and quantitative candidate selection.

## Verdict Caps

Use `off` when chart class changes, topology or footprint is below 60, mixed
panels are lost, routed surface floors remain, or labels/ticks/z-labels,
annotations, legends, or colorbars collide or clip. Use at most `close` when
class is recognizable but footprint, camera/elevation, box aspect, occupancy, or
panel read path materially drifts.

`ship` requires L1-consistent topology, footprint, camera/aspect, occupancy,
surface/mark style, color semantics, text placement, and final export floor.

## Required JSON

Add top-level `three_d_scorecard` with integer 0-100 fields:
`topology`, `geometry_footprint`, `camera_box_aspect`,
`composition_occupancy`, `surface_or_mark_style`, `color_semantics`,
`text_export_floor`, `overall`, plus string `summary`.

Also include `target_dimension`, `movement_delta` (`accepted`, `too_small`,
`wrong_direction`, `overshoot`, `right_direction_with_regression`, or
`no_visible_delta`), `must_preserve`, `candidate_regressions`,
`remaining_gaps`, `next_single_change`, and `accept_over_control`.

Score baseline, control, and candidate separately when present. A candidate can
beat baseline and still lose to the accepted control.

## Dimension Meanings

- `topology`: representation class, subtype skeleton, surface/mark roles.
- `geometry_footprint`: projected spread, proportions, extrema, clusters, bars,
  paths, layers, compactness, relief, and side/basin footprint.
- `camera_box_aspect`: quadrant/elevation, front edge, z-wall, box proportions.
- `composition_occupancy`: crop, whitespace, plot-box, panel ratios, anchors,
  and subject size.
- `surface_or_mark_style`: mesh/facet/smoothness, marker/bar/layer/path style.
- `color_semantics`: palette family, material/lighting, scalar/categorical
  meaning, colorbar/legend anchors.
- `text_export_floor`: readable titles, labels, ticks, legends, colorbars,
  annotations, and no clipping/collision.

`overall` weights: topology 20%, footprint 24%, camera/aspect 16%,
composition/occupancy 12%, style 10%, color 8%, text/export 10%.

Strict pass requires `overall >= 85`, no dimension below 75, topology,
footprint, camera/aspect, and composition/occupancy at least 82, and no active
floor. Otherwise `strict_pass=false`.

## Acceptance

Accept over control only when the active low-scoring dimension visibly improves
at final size and no primary record regresses. Prefer conservative on ties.
Large scale, stronger z range, or changed camera is improvement only if it is
closer to L1 and preserves export readability.

`focus_themes` must target the lowest one or two 3D dimensions before polish.
