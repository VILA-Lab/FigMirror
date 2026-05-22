# 3D Candidate Selection

Read only in strict reproduction or quantitative candidate diagnosis.

## Rendered Outputs Only

Only final-export rendered images can win. The last render does not
automatically win. Keep an earlier candidate when it is closer across topology,
footprint, camera/aspect, occupancy, style, color, and export floor.

## Control Lock

Compare every candidate against the accepted rendered control. A new candidate
wins only if it visibly improves the named active record and does not regress
primary records. Ties keep the conservative/control render; larger scale,
stronger z range, or a cleaner view is not improvement by itself.

Repairs preserve accepted camera, limits, crop, scale, palette/material,
text/export, and mark/mesh family unless that exact record is the active target.
Independent redraws are probes until rebuilt under those locks.

## Delta Contract

For footprint, camera/aspect, scale, occupancy, surface, or mark repairs, record
2-4 visible landmarks, intended movement direction, preserve locks, and reject-if
tests. Hidden parameter changes, bbox noise, rerenders, or local color/roughness
tweaks do not count as repair when no final-size visual record moves.

If two attempts on the same low primary record are rejected or near-identical,
switch construction family or render a small/moderate/strong bracket under the
same export settings. Move one primary record at a time unless chart class is
wrong.

## Non-Regression

Reject candidates that improve a local cue while worsening shape, camera/crop,
occupancy, overlays, color/material semantics, crease graph, or clean export.
If every probe regresses, export the accepted control again and name the missing
record for the next attempt.
