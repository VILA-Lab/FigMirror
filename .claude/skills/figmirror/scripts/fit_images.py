#!/usr/bin/env python3
"""Fit staged audit-view images to the Claude Code delivery limit.

Claude Code downscales any image whose long edge exceeds 2000 px before the
model sees it. Doing that transform here instead makes the delivered pixels a
recorded property of the run rather than a host-side side effect.

This file is Claude-harness adaptation. It is ADDED alongside the ported Codex
bundle and never edits it: `figannot.py` and every `references/*.md` carried
over from `.codex/skills/figmirror` stay byte-identical to their source, as
recorded in MANIFEST.md.

Rules:
  - Only resize when the long edge exceeds the limit. A compliant image is left
    byte-identical; it is not re-encoded.
  - Always PNG in, PNG out. Never transcode to JPEG.
  - Resize in place, and only ever on a staged copy inside `audit_view_<N>/`.
    Everything else in the workdir is either an original or carries geometry
    that other steps depend on, so the check is on the containing directory,
    not on the filename. Filename matching cannot express this invariant:
    `reference_clean.png` exists both at `inputs/reference_clean.png` (the L1
    measurement anchor the Drawer measures against, must never be touched) and
    at `audit_view_<N>/reference_clean.png` (a staged copy, must be fitted).
    `composite.png` is then the one exception inside that directory:
    `figannot.py compose` records its native W/H/draft_x/draft_w in
    `composite_meta.json`, the Reviewer treats those as hard coordinate bounds,
    and `figannot.py draw` clamps and renders the returned boxes against the
    same native metadata. Resizing it without resizing the metadata puts every
    drawn box off-target while `draw` still exits 0.
  - Emit one JSON object per image so the applied factor can be recorded in
    `process.md` and used later to separate native-resolution reviews from
    heavily downscaled ones.

Usage:
    python fit_images.py --max-edge 2000 IMAGE [IMAGE ...]
    python fit_images.py --max-edge 2000 --report out.json IMAGE [IMAGE ...]

Missing paths are skipped and reported with `"status": "missing"`, because
`accepted_control.png` is present only on strict-3D iterations.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - environment problem, not logic
    sys.stderr.write(
        "fit_images.py requires Pillow. Invoke it through the run's PYTHON_CMD "
        "(for example `uv run --with pillow python`) rather than a bare "
        "interpreter.\n"
    )
    raise SystemExit(2)


AUDIT_VIEW = re.compile(r"^audit_view_\d+$")


def refuse(path: Path) -> str | None:
    """Return a reason string if `path` must never be resized.

    Resolve first, so a symlink or a `./` prefix cannot dress a protected
    original up as a staged copy.
    """
    resolved = path.resolve()
    if not AUDIT_VIEW.match(resolved.parent.name):
        return (
            f"only staged copies directly inside audit_view_<N>/ may be fitted; "
            f"{resolved} sits in {resolved.parent.name or '/'}. Originals such as "
            "inputs/reference_clean.png (the L1 measurement anchor), "
            "inputs/reference_raw.png, img_iter<N>.png and "
            "review_feedback_<N>/annotated.png must keep their native pixels"
        )
    if resolved.name == "composite.png":
        return (
            "composite.png carries the coordinate frame recorded in "
            "composite_meta.json; resizing it silently offsets every box that "
            "figannot.py draw renders"
        )
    return None


def fit_one(path: Path, max_edge: int) -> dict:
    """Downscale `path` in place if its long edge exceeds `max_edge`."""
    if not path.exists():
        return {"path": str(path), "status": "missing"}

    with Image.open(path) as img:
        img.load()
        width, height = img.size
        long_edge = max(width, height)
        record = {
            "path": str(path),
            "native_width": width,
            "native_height": height,
            "max_edge": max_edge,
        }

        if long_edge <= max_edge:
            record.update(
                {
                    "status": "unchanged",
                    "scale": 1.0,
                    "delivered_width": width,
                    "delivered_height": height,
                }
            )
            return record

        scale = max_edge / long_edge
        # round() rather than int(): int() truncates and can land a hair under
        # the limit on one axis while the other is exact, which reads as an
        # off-by-one when the report is compared against the image.
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        resized = img.resize(new_size, Image.LANCZOS)
        # Preserve the alpha channel: composite.png is built on a light canvas
        # and flattening it would change what the Reviewer sees.
        resized.save(path, format="PNG")

    record.update(
        {
            "status": "resized",
            "scale": round(scale, 6),
            "delivered_width": new_size[0],
            "delivered_height": new_size[1],
        }
    )
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--max-edge", type=int, default=2000)
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write the JSON records to this file as well as stdout",
    )
    args = parser.parse_args(argv)

    if args.max_edge < 1:
        parser.error("--max-edge must be positive")

    # Fail before touching anything: a partially-applied run is harder to
    # reason about than a refused one.
    refusals = [(p, r) for p in args.images if (r := refuse(p))]
    if refusals:
        for path, reason in refusals:
            sys.stderr.write(f"refusing {path}: {reason}\n")
        return 2

    # A pixel change with no record is the exact failure this script exists to
    # prevent, so the report is written even when a later image blows up.
    records: list[dict] = []
    try:
        for path in args.images:
            record = fit_one(path, args.max_edge)
            records.append(record)
            print(json.dumps(record, sort_keys=True))
    finally:
        if args.report is not None:
            args.report.write_text(
                json.dumps(records, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
