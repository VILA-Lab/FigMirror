# 3D Material And Lighting

Read when color reads as material, illumination, or shadow rather than scalar
data, especially when L1 has no colorbar.

First classify color ownership:

- data color: visible colorbar, scalar legend, or continuous value encoding.
- material lighting: highlights/shadows tied to normals, orientation, or
  occlusion.

Do not replace material lighting with rainbow, red-blue, or heatmap gradients
unless L1 shows a scalar encoding. For material surfaces, infer highlight,
midtone, shadow, and sparse dark-accent families from visible regions. Apply
them through lighting, normals, face colors, and shade contrast so the subject
reads as one lit material.

Material repair changes only palette, lighting, normal-driven highlights,
facecolor blending, or roughness. Reject candidates that add contour-like marks,
stripes, stains, colorbar-like ramps, dense wire artifacts, transparency washout,
or hide cavities/facets. Material gains cannot compensate for topology,
footprint, camera/aspect, occupancy, or export regressions.
