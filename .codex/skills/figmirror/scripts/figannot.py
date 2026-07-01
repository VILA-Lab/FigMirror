#!/usr/bin/env python3
"""FigMirror annotation operator: deterministic compose + draw.

The Orchestrator calls this script; the model does not hand-write annotation
code. It builds a normalized far-view composite, then draws Reviewer-returned
boxes into an annotated image and notes file for the next Drawer invocation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import matplotlib
import numpy as np
from PIL import Image, ImageDraw, ImageFont


_COLORS = [
    (230, 30, 30),
    (30, 120, 235),
    (30, 175, 80),
    (210, 120, 20),
    (165, 60, 205),
]


def _font(size: int):
    try:
        return ImageFont.truetype(
            str(Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans-Bold.ttf"),
            size,
        )
    except Exception:
        return ImageFont.load_default()


def _projection_intervals(
    proj: np.ndarray, *, frac: float, min_len: int
) -> tuple[list[tuple[int, int]], float]:
    if proj.size == 0 or float(proj.max(initial=0)) <= 0:
        return [], 0.0
    threshold = float(proj.max()) * frac
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for idx, value in enumerate(proj):
        if value >= threshold and start is None:
            start = idx
        elif value < threshold and start is not None:
            if idx - start >= min_len:
                runs.append((start, idx - 1))
            start = None
    if start is not None and len(proj) - start >= min_len:
        runs.append((start, len(proj) - 1))
    return runs, threshold


def _side_dark_pixel_diagnostics(image: Image.Image) -> dict:
    """Return coarse side-text cues around large colored panel regions.

    This is a deterministic attention cue for the Reviewer, not an oracle:
    dark pixels can be tick labels, axis labels, spines, marks, or neighboring
    text. The prompt tells the Reviewer to verify visually before acting on it.
    """
    arr = np.asarray(image.convert("RGB")).astype(np.int16)
    height, width = arr.shape[:2]
    maxc = arr.max(axis=2)
    minc = arr.min(axis=2)
    color_mask = ((maxc - minc) > 35) & (maxc < 248)
    dark_mask = maxc < 120

    x_runs, _ = _projection_intervals(
        color_mask.sum(axis=0), frac=0.06, min_len=max(10, width // 100)
    )
    y_runs, _ = _projection_intervals(
        color_mask.sum(axis=1), frac=0.06, min_len=max(10, height // 100)
    )

    def left_right_bias(side_dark: dict[str, int]) -> str:
        left = int(side_dark.get("left", 0))
        right = int(side_dark.get("right", 0))
        larger = max(left, right, 1)
        if abs(left - right) < max(40, int(larger * 0.22)):
            return "balanced"
        return "left_heavier" if left > right else "right_heavier"

    panels: list[dict] = []
    for row, (y0, y1) in enumerate(y_runs[:6]):
        for col, (x0, x1) in enumerate(x_runs[:8]):
            panel_w = x1 - x0 + 1
            panel_h = y1 - y0 + 1
            if panel_w < 40 or panel_h < 40:
                continue
            # Slim saturated regions are usually colorbars; keep the diagnostic
            # focused on panel-like regions.
            if panel_w < panel_h * 0.35:
                continue
            side_w = max(3, int(panel_w * 0.18))
            top_h = max(3, int(panel_h * 0.12))
            bottom_h = max(3, int(panel_h * 0.16))
            left0, left1 = max(0, x0 - side_w), max(0, x0)
            right0, right1 = min(width, x1 + 1), min(width, x1 + 1 + side_w)
            top0, top1 = max(0, y0 - top_h), max(0, y0)
            bottom0, bottom1 = min(height, y1 + 1), min(height, y1 + 1 + bottom_h)

            side_dark = {
                "left": int(dark_mask[y0 : y1 + 1, left0:left1].sum())
                if left1 > left0
                else 0,
                "right": int(dark_mask[y0 : y1 + 1, right0:right1].sum())
                if right1 > right0
                else 0,
                "top": int(dark_mask[top0:top1, x0 : x1 + 1].sum())
                if top1 > top0
                else 0,
                "bottom": int(dark_mask[bottom0:bottom1, x0 : x1 + 1].sum())
                if bottom1 > bottom0
                else 0,
            }
            panels.append(
                {
                    "row": row,
                    "col": col,
                    "box": [int(x0), int(y0), int(x1), int(y1)],
                    "side_dark_px": side_dark,
                    "left_right_bias": left_right_bias(side_dark),
                }
            )

    bias_pattern = [
        f"r{panel['row']}c{panel['col']}:{panel['left_right_bias']}"
        for panel in panels[:12]
    ]
    return {
        "available": bool(panels),
        "method": (
            "large colored-region panel boxes; dark-pixel counts in adjacent "
            "side bands; cue only, visual verification required"
        ),
        "left_right_bias_pattern": bias_pattern,
        "panels": panels[:12],
    }


def _local_layout_diagnostics(ref: Image.Image, draft: Image.Image) -> dict:
    try:
        return {
            "reference": _side_dark_pixel_diagnostics(ref),
            "draft": _side_dark_pixel_diagnostics(draft),
        }
    except Exception as exc:  # pragma: no cover - defensive cue only
        return {"available": False, "error": str(exc)}


def cmd_compose(args) -> None:
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ref = Image.open(args.ref).convert("RGB")
    draft = Image.open(args.draft).convert("RGB")
    target_height = args.height
    gutter = args.gutter

    def scale(image: Image.Image) -> Image.Image:
        return image.resize(
            (max(1, int(image.width * target_height / image.height)), target_height)
        )

    ref_scaled = scale(ref)
    draft_scaled = scale(draft)
    width = ref_scaled.width + gutter + draft_scaled.width
    composite = Image.new("RGB", (width, target_height), (255, 255, 255))
    composite.paste(ref_scaled, (0, 0))
    composite.paste(draft_scaled, (ref_scaled.width + gutter, 0))
    composite.save(out / "composite.png")

    meta = {
        "W": width,
        "H": target_height,
        "draft_x": ref_scaled.width + gutter,
        "draft_w": draft_scaled.width,
    }
    diagnostics = _local_layout_diagnostics(ref, draft)
    meta["local_layout_diagnostics"] = diagnostics

    base = ""
    if args.reviewer_md and Path(args.reviewer_md).is_file():
        base = Path(args.reviewer_md).read_text(encoding="utf-8")

    dims = (
        "\n\n---\n"
        "You are shown three images: the side-by-side composite "
        f"({width}x{target_height}px, REFERENCE left | DRAFT right, "
        f"draft panel at x={meta['draft_x']}, {meta['draft_w']}px wide), "
        "the full-resolution REFERENCE, and the full-resolution DRAFT. Put every "
        "box's coordinates in PIXELS on the COMPOSITE, around the DRAFT side. "
        f"Keep every box inside x={meta['draft_x']}..{meta['draft_x'] + meta['draft_w']} "
        f"and y=0..{target_height}."
    )
    diag = (
        "\n\n---\n"
        "## ORCHESTRATOR-STAGED LOCAL LAYOUT DIAGNOSTICS - supporting cue only\n\n"
        "The JSON below estimates dark-pixel density in side bands around large "
        "colored panel regions. Use it to aim your visual inspection for "
        "coordinate-side and label-band issues; verify against the images before "
        "anchoring or critiquing. If reference and draft have opposite "
        "`left_right_bias_pattern` entries, inspect whether coordinate text moved "
        "sides; anchor local layout register only after you can explain the "
        "difference from L1, such as a colorbar rather than panel coordinates.\n\n"
        + json.dumps(diagnostics, ensure_ascii=True, separators=(",", ":"))
    )

    anchors = ""
    if args.anchors_md and Path(args.anchors_md).is_file():
        body = Path(args.anchors_md).read_text(encoding="utf-8").strip()
        if body:
            anchors = (
                "\n\n---\n"
                "## CONFIRMED CORRECT - already verified to match; build on these\n\n"
                + body
                + "\n"
            )

    changed = ""
    if args.changed_md and Path(args.changed_md).is_file():
        body = Path(args.changed_md).read_text(encoding="utf-8").strip()
        if body:
            changed = (
                "\n\n---\n"
                "## JUST CHANGED by the Drawer - verify each now matches\n\n"
                + body
                + "\n"
            )

    (out / "review_prompt.txt").write_text(
        base + dims + diag + anchors + changed, encoding="utf-8"
    )
    (out / "composite_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    print(json.dumps(meta))


def _parse_review(path: Path) -> dict:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


def _confirmed_items(review: dict) -> list[str]:
    items: list[str] = []
    for key in ("confirmed_good",):
        for item in review.get(key, []) or []:
            text = str(item).strip()
            if text:
                items.append(text)
    anchor = review.get("anchor") if isinstance(review.get("anchor"), dict) else {}
    for item in anchor.get("what_is_right", []) or []:
        text = str(item).strip()
        if text:
            items.append(text)
    return items


def _review_verdict_and_summary(review: dict) -> tuple[str, str]:
    fidelity = review.get("fidelity") if isinstance(review.get("fidelity"), dict) else {}
    floor = (
        review.get("quality_floor")
        if isinstance(review.get("quality_floor"), dict)
        else {}
    )
    verdict = str(fidelity.get("verdict") or review.get("verdict") or "?")
    summary = str(floor.get("summary") or review.get("summary") or "")
    return verdict, summary


def _read_meta(out: Path, image: Image.Image) -> dict[str, int]:
    meta = {"W": image.width, "H": image.height, "draft_x": 0, "draft_w": image.width}
    path = out / "composite_meta.json"
    if not path.is_file():
        return meta
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return meta
    for key in meta:
        try:
            meta[key] = int(loaded[key])
        except Exception:
            pass
    return meta


def _normalize_boxes(boxes: list, meta: dict[str, int]) -> tuple[list[dict], list[str]]:
    width = max(1, int(meta["W"]))
    height = max(1, int(meta["H"]))
    draft_x = max(0, min(int(meta["draft_x"]), width - 1))
    draft_right = max(draft_x + 1, min(width, draft_x + max(1, int(meta["draft_w"]))))

    normalized: list[dict] = []
    repairs: list[str] = []
    for idx, box in enumerate(boxes, 1):
        if not isinstance(box, dict):
            repairs.append(f"box {idx}: skipped non-object box")
            continue
        try:
            orig = (
                int(box["x0"]),
                int(box["y0"]),
                int(box["x1"]),
                int(box["y1"]),
            )
        except Exception:
            repairs.append(f"box {idx}: skipped missing integer x0/y0/x1/y1")
            continue

        x0 = max(draft_x, min(orig[0], draft_right - 1))
        y0 = max(0, min(orig[1], height - 1))
        x1 = max(draft_x + 1, min(orig[2], draft_right))
        y1 = max(1, min(orig[3], height))
        if x0 >= x1 or y0 >= y1:
            repairs.append(f"box {idx}: skipped degenerate box after bounds normalization")
            continue
        fixed = dict(box)
        fixed.update({"x0": x0, "y0": y0, "x1": x1, "y1": y1})
        if (x0, y0, x1, y1) != orig:
            repairs.append(
                f"box {idx}: clamped {orig} to {(x0, y0, x1, y1)} "
                f"inside DRAFT x-range {draft_x}..{draft_right}"
            )
        normalized.append(fixed)
    return normalized, repairs


def _mirror_audit_iter(out: Path, review_text: str) -> None:
    match = re.fullmatch(r"audit_view_(\d+)", out.name)
    if not match:
        return
    mirror = out.parent / f"audit_iter{match.group(1)}.json"
    if mirror.exists():
        mirror.write_text(review_text, encoding="utf-8")


def cmd_prepare(args) -> None:
    workdir = Path(args.workdir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    iter_idx = int(args.iter)

    anchors: set[str] = set()
    for prior in range(iter_idx):
        review = _parse_review(workdir / f"audit_view_{prior}" / "review.json")
        for item in _confirmed_items(review):
            anchors.add(item)
    (out / "anchors.md").write_text(
        "".join(f"- {item}\n" for item in sorted(anchors)),
        encoding="utf-8",
    )

    changed: list[str] = []
    if iter_idx > 0:
        previous_notes = workdir / f"audit_view_{iter_idx - 1}" / "notes.md"
        if previous_notes.is_file():
            for line in previous_notes.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                match = re.match(r"^\s*\d+\.\s+(.*\S)\s*$", line)
                if match:
                    changed.append(match.group(1))
    (out / "changed.md").write_text(
        "".join(f"- {item}\n" for item in changed),
        encoding="utf-8",
    )


def cmd_draw(args) -> None:
    out = Path(args.out_dir)
    review = _parse_review(out / "review.json")
    boxes = review.get("boxes", []) if isinstance(review, dict) else []
    if not isinstance(boxes, list):
        boxes = []
    image = Image.open(out / "composite.png").convert("RGB")
    meta = _read_meta(out, image)
    boxes, repairs = _normalize_boxes(boxes, meta)
    if isinstance(review, dict):
        review["boxes"] = boxes
        if repairs:
            review["box_coordinate_repairs"] = repairs
        review_text = json.dumps(review, ensure_ascii=False, indent=2) + "\n"
        (out / "review.json").write_text(review_text, encoding="utf-8")
        _mirror_audit_iter(out, review_text)
    verdict, summary = _review_verdict_and_summary(review)
    draw = ImageDraw.Draw(image)
    font = _font(34)
    notes = [
        f"verdict: {verdict}",
        "",
        f"summary: {summary}",
        "",
        "The numbered red/blue/green/... boxes on the annotated composite mark recent "
        "DRAFT-side mismatches against the REFERENCE. Re-check each area, preserve it "
        "when it now matches the reference's visual class, and repair only unresolved "
        "mismatches:",
    ]
    if repairs:
        notes.extend(["", "Box coordinate repairs:"])
        notes.extend(f"  - {item}" for item in repairs)
        notes.append("")
    drawn = 0
    for idx, box in enumerate(boxes, 1):
        try:
            xy = [int(box["x0"]), int(box["y0"]), int(box["x1"]), int(box["y1"])]
        except Exception:
            continue
        color = _COLORS[(idx - 1) % len(_COLORS)]
        draw.rectangle(xy, outline=color, width=5)
        label_x = xy[0]
        label_y = max(0, xy[1] - 40)
        draw.rectangle([label_x, label_y, label_x + 38, label_y + 40], fill=color)
        draw.text((label_x + 9, label_y + 1), str(idx), fill=(255, 255, 255), font=font)
        notes.append(f"  {idx}. {box.get('note') or box.get('label', '')}")
        drawn += 1
    image.save(out / "annotated.png")
    (out / "notes.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    print(f"verdict={verdict} boxes_drawn={drawn}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="figannot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    compose = sub.add_parser("compose")
    compose.add_argument("--ref", required=True)
    compose.add_argument("--draft", required=True)
    compose.add_argument("--out-dir", required=True)
    compose.add_argument("--reviewer-md", default="")
    compose.add_argument("--anchors-md", default="")
    compose.add_argument("--changed-md", default="")
    compose.add_argument("--height", type=int, default=920)
    compose.add_argument("--gutter", type=int, default=28)
    compose.set_defaults(func=cmd_compose)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--workdir", required=True)
    prepare.add_argument("--iter", required=True)
    prepare.add_argument("--out-dir", required=True)
    prepare.set_defaults(func=cmd_prepare)

    draw = sub.add_parser("draw")
    draw.add_argument("--out-dir", required=True)
    draw.set_defaults(func=cmd_draw)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
