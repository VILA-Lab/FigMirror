#!/usr/bin/env python3
"""Score rendered 3D candidate PNGs against a reference image.

This helper is intentionally generic: it compares visible raster properties
that matter for 3D reproduction candidates, then emits JSON for the Drawer or
Orchestrator to use as a tie-breaker. It does not inspect source data or infer
hidden values.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat


CANVAS = (320, 320)
WEIGHTS = {
    "pixel_similarity": 0.04,
    "edge_structure": 0.09,
    "neutral_structure": 0.08,
    "silhouette_iou": 0.05,
    "composition_center": 0.04,
    "composition_scale": 0.06,
    "primary_geometry_iou": 0.16,
    "primary_geometry_center": 0.06,
    "primary_geometry_scale": 0.08,
    "primary_geometry_layout": 0.12,
    "foreground_occupancy": 0.04,
    "palette_overlap": 0.05,
    "gradient_layout": 0.04,
    "export_floor": 0.03,
    "three_d_proxy": 0.06,
}
WEIGHT_TOTAL = sum(WEIGHTS.values())
SCORECARD_PROXY_WEIGHTS = {
    "topology": 0.20,
    "geometry_footprint": 0.24,
    "camera_box_aspect": 0.16,
    "composition_occupancy": 0.12,
    "surface_or_mark_style": 0.10,
    "color_semantics": 0.08,
    "text_export_floor": 0.10,
}
PRIMARY_SCORECARD_DIMENSIONS = (
    "topology",
    "geometry_footprint",
    "camera_box_aspect",
    "composition_occupancy",
    "text_export_floor",
)
ROLE_TARGET_SCORECARD_DIMENSIONS = {
    "topology_repair": ("topology",),
    "path_footprint_repair": ("geometry_footprint",),
    "scatter_hull_repair": ("topology", "geometry_footprint"),
    "trajectory_camera_probe": ("camera_box_aspect", "geometry_footprint"),
    "camera_register_probe": ("camera_box_aspect", "geometry_footprint"),
    "aspect_or_camera_repair": ("camera_box_aspect",),
    "layout_containment": ("composition_occupancy", "text_export_floor"),
    "silhouette_repair": ("topology",),
    "occupancy_repair": ("composition_occupancy",),
    "palette_repair": ("color_semantics",),
    "export_floor_repair": ("text_export_floor",),
    "geometry_repair": ("geometry_footprint",),
    "three_d_proxy_repair": ("topology", "geometry_footprint", "camera_box_aspect"),
}
REQUIRED_ROLES = ("conservative_l1",)
ALLOWED_ROLES = {
    "base_path_control",
    "conservative_l1",
    "aspect_or_camera_repair",
    "layout_containment",
    "topology_repair",
    "path_footprint_repair",
    "scatter_hull_repair",
    "trajectory_camera_probe",
    "camera_register_probe",
    "silhouette_repair",
    "occupancy_repair",
    "palette_repair",
    "export_floor_repair",
    "geometry_repair",
    "three_d_proxy_repair",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rank 3D candidate PNGs by visible similarity to a reference.",
    )
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--candidate", required=True, nargs="+", type=Path)
    parser.add_argument(
        "--role",
        nargs="*",
        help=(
            "Optional sanitized role for each candidate. Use only generic roles "
            "such as base_path_control, conservative_l1, aspect_or_camera_repair, "
            "camera_register_probe, trajectory_camera_probe, scatter_hull_repair, "
            "or layout_containment."
        ),
    )
    parser.add_argument("--out", type=Path, help="Optional JSON output path.")
    parser.add_argument(
        "--canvas",
        type=int,
        default=CANVAS[0],
        help="Square normalization size in pixels. Default: 320.",
    )
    parser.add_argument(
        "--allow-unspecified-roles",
        action="store_true",
        help=(
            "Allow diagnostic comparisons without role metadata. Do not use "
            "for product candidate sweeps."
        ),
    )
    parser.add_argument(
        "--strict-primary-floor",
        type=float,
        default=0.0,
        help=(
            "Optional strict floor for topology, geometry footprint, and "
            "camera/box-aspect proxy dimensions. Use 82 for strict scatter or "
            "trajectory reproduction when a 0-100 visual ship gate is required."
        ),
    )
    return parser.parse_args()


def resolve_roles(candidate_count: int, raw_roles: list[str] | None) -> list[str]:
    if raw_roles is None:
        return ["unspecified"] * candidate_count
    if len(raw_roles) != candidate_count:
        raise SystemExit("--role must be omitted or have exactly one value per candidate.")
    unknown = sorted(set(raw_roles) - ALLOWED_ROLES)
    if unknown:
        raise SystemExit(
            "Unsupported role(s): "
            + ", ".join(unknown)
            + ". Use generic candidate roles only."
        )
    return raw_roles


def clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def load_image(path: Path, size: tuple[int, int]) -> Image.Image:
    image = Image.open(path).convert("RGB")
    image.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, "white")
    offset = ((size[0] - image.width) // 2, (size[1] - image.height) // 2)
    canvas.paste(image, offset)
    return canvas


def rms_mse(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    return sum(value * value for value in stat.rms) / 3.0


def pixel_similarity(ref: Image.Image, cand: Image.Image) -> float:
    rms = math.sqrt(rms_mse(ref, cand))
    return clamp(100.0 * (1.0 - rms / 115.0))


def edge_structure(ref: Image.Image, cand: Image.Image) -> float:
    ref_edges = ImageOps.grayscale(ref).filter(ImageFilter.FIND_EDGES).convert("RGB")
    cand_edges = ImageOps.grayscale(cand).filter(ImageFilter.FIND_EDGES).convert("RGB")
    rms = math.sqrt(rms_mse(ref_edges, cand_edges))
    return clamp(100.0 * (1.0 - rms / 95.0))


def ink_mask(image: Image.Image, threshold: int = 248) -> Image.Image:
    gray = ImageOps.grayscale(image)
    return gray.point(lambda pixel: 255 if pixel < threshold else 0)


def mask_from_predicate(image: Image.Image, predicate) -> Image.Image:
    mask = Image.new("L", image.size)
    mask.putdata([255 if predicate(pixel) else 0 for pixel in image.getdata()])
    return mask


def neutral_mask(image: Image.Image) -> Image.Image:
    def neutral(pixel: tuple[int, int, int]) -> bool:
        red, green, blue = pixel
        brightness = (red + green + blue) / 3
        spread = max(pixel) - min(pixel)
        return spread <= 28 and 45 <= brightness <= 245

    return mask_from_predicate(image, neutral)


def remove_peripheral_color_guides(mask: Image.Image) -> Image.Image:
    width, height = mask.size
    data = list(mask.getdata())
    visited = bytearray(width * height)
    keep = bytearray(width * height)

    def neighbors(index: int) -> tuple[int, ...]:
        x = index % width
        y = index // width
        result = []
        if x > 0:
            result.append(index - 1)
        if x + 1 < width:
            result.append(index + 1)
        if y > 0:
            result.append(index - width)
        if y + 1 < height:
            result.append(index + width)
        return tuple(result)

    components: list[tuple[list[int], tuple[int, int, int, int]]] = []
    for start, value in enumerate(data):
        if not value or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        pixels = []
        x0 = width
        y0 = height
        x1 = 0
        y1 = 0
        while stack:
            index = stack.pop()
            pixels.append(index)
            x = index % width
            y = index // width
            x0 = min(x0, x)
            y0 = min(y0, y)
            x1 = max(x1, x + 1)
            y1 = max(y1, y + 1)
            for next_index in neighbors(index):
                if data[next_index] and not visited[next_index]:
                    visited[next_index] = 1
                    stack.append(next_index)
        components.append((pixels, (x0, y0, x1, y1)))

    kept_pixels = 0
    for pixels, (x0, y0, x1, y1) in components:
        box_width = (x1 - x0) / width
        box_height = (y1 - y0) / height
        center_x = (x0 + x1) / (2 * width)
        center_y = (y0 + y1) / (2 * height)
        peripheral_vertical = box_width < 0.14 and box_height > 0.25 and center_x > 0.64
        peripheral_swatch = (
            box_width < 0.22
            and box_height < 0.20
            and (center_x < 0.18 or center_x > 0.72 or center_y < 0.18)
        )
        if peripheral_vertical or peripheral_swatch or len(pixels) < 3:
            continue
        for index in pixels:
            keep[index] = 255
        kept_pixels += len(pixels)

    if kept_pixels / max(1, width * height) < 0.005:
        return mask
    result = Image.new("L", mask.size)
    result.putdata(keep)
    return result


def primary_geometry_mask(image: Image.Image) -> Image.Image:
    def saturated(pixel: tuple[int, int, int]) -> bool:
        red, green, blue = pixel
        brightness = (red + green + blue) / 3
        spread = max(pixel) - min(pixel)
        return spread > 32 and 35 <= brightness <= 250

    mask = mask_from_predicate(image, saturated)
    if mask.getbbox() is None:
        return ink_mask(image)
    mask = remove_peripheral_color_guides(mask)
    if mask.getbbox() is None:
        return ink_mask(image)
    width, height = mask.size
    x0, y0, x1, y1 = mask.getbbox()
    if ((x1 - x0) * (y1 - y0)) / max(1, width * height) < 0.01:
        return ink_mask(image)
    return mask


def bbox_features_from_mask(mask: Image.Image) -> dict[str, object]:
    bbox = mask.getbbox()
    if bbox is None:
        return {
            "center": (0.5, 0.5),
            "size": (0.0, 0.0),
            "area": 0.0,
            "aspect": 1.0,
        }
    width, height = mask.size
    x0, y0, x1, y1 = bbox
    box_width = (x1 - x0) / width
    box_height = (y1 - y0) / height
    return {
        "center": (((x0 + x1) / 2) / width, ((y0 + y1) / 2) / height),
        "size": (box_width, box_height),
        "area": box_width * box_height,
        "aspect": box_width / box_height if box_height else 1.0,
    }


def bbox_features(image: Image.Image) -> dict[str, object]:
    return bbox_features_from_mask(ink_mask(image))


def mask_iou(ref_mask: Image.Image, cand_mask: Image.Image) -> float:
    inter = 0
    union = 0
    for ref_pixel, cand_pixel in zip(ref_mask.getdata(), cand_mask.getdata()):
        ref_on = ref_pixel > 0
        cand_on = cand_pixel > 0
        inter += int(ref_on and cand_on)
        union += int(ref_on or cand_on)
    return clamp(100.0 * inter / union) if union else 0.0


def silhouette_iou(ref: Image.Image, cand: Image.Image) -> float:
    return mask_iou(ink_mask(ref), ink_mask(cand))


def neutral_structure(ref: Image.Image, cand: Image.Image) -> float:
    return mask_iou(neutral_mask(ref), neutral_mask(cand))


def primary_geometry_iou(ref: Image.Image, cand: Image.Image) -> float:
    return mask_iou(primary_geometry_mask(ref), primary_geometry_mask(cand))


def primary_geometry_center(ref: Image.Image, cand: Image.Image) -> float:
    ref_features = bbox_features_from_mask(primary_geometry_mask(ref))
    cand_features = bbox_features_from_mask(primary_geometry_mask(cand))
    return clamp(100.0 - 180.0 * math.dist(ref_features["center"], cand_features["center"]))


def primary_geometry_scale(ref: Image.Image, cand: Image.Image) -> float:
    ref_features = bbox_features_from_mask(primary_geometry_mask(ref))
    cand_features = bbox_features_from_mask(primary_geometry_mask(cand))
    size_delta = math.dist(ref_features["size"], cand_features["size"])
    aspect_delta = abs(
        math.log(max(cand_features["aspect"], 0.01) / max(ref_features["aspect"], 0.01))
    )
    return clamp(100.0 - (125.0 * size_delta + 35.0 * aspect_delta))


def primary_geometry_layout(ref: Image.Image, cand: Image.Image) -> float:
    def bins(mask: Image.Image) -> list[float]:
        width, height = mask.size
        values = []
        for by in range(3):
            for bx in range(3):
                crop = mask.crop(
                    (
                        bx * width // 3,
                        by * height // 3,
                        (bx + 1) * width // 3,
                        (by + 1) * height // 3,
                    )
                )
                values.append(sum(1 for pixel in crop.getdata() if pixel > 0))
        total = sum(values) or 1
        return [value / total for value in values]

    distance = sum(
        abs(a - b)
        for a, b in zip(bins(primary_geometry_mask(ref)), bins(primary_geometry_mask(cand)))
    )
    return clamp(100.0 * (1.0 - distance / 1.4))


def composition_center(ref: Image.Image, cand: Image.Image) -> float:
    ref_features = bbox_features(ref)
    cand_features = bbox_features(cand)
    return clamp(100.0 - 180.0 * math.dist(ref_features["center"], cand_features["center"]))


def composition_scale(ref: Image.Image, cand: Image.Image) -> float:
    ref_features = bbox_features(ref)
    cand_features = bbox_features(cand)
    size_delta = math.dist(ref_features["size"], cand_features["size"])
    aspect_delta = abs(
        math.log(max(cand_features["aspect"], 0.01) / max(ref_features["aspect"], 0.01))
    )
    return clamp(100.0 - (115.0 * size_delta + 30.0 * aspect_delta))


def foreground_occupancy(ref: Image.Image, cand: Image.Image) -> float:
    ref_features = bbox_features(ref)
    cand_features = bbox_features(cand)
    if ref_features["area"] <= 0:
        return 0.0
    area_ratio = max(cand_features["area"] / ref_features["area"], 0.01)
    return clamp(100.0 - 85.0 * abs(math.log(area_ratio)))


def palette_overlap(ref: Image.Image, cand: Image.Image) -> float:
    def histogram(image: Image.Image) -> dict[tuple[int, int, int], float]:
        small = image.resize((160, 160), Image.Resampling.BILINEAR)
        bins: dict[tuple[int, int, int], int] = {}
        for red, green, blue in small.getdata():
            if red > 245 and green > 245 and blue > 245:
                continue
            key = (red // 32, green // 32, blue // 32)
            bins[key] = bins.get(key, 0) + 1
        total = sum(bins.values()) or 1
        return {key: value / total for key, value in bins.items()}

    ref_hist = histogram(ref)
    cand_hist = histogram(cand)
    keys = set(ref_hist) | set(cand_hist)
    return clamp(100.0 * sum(min(ref_hist.get(key, 0), cand_hist.get(key, 0)) for key in keys))


def gradient_layout(ref: Image.Image, cand: Image.Image) -> float:
    def bins(image: Image.Image) -> list[float]:
        edge = ImageOps.grayscale(image).filter(ImageFilter.FIND_EDGES)
        width, height = edge.size
        values = []
        for by in range(3):
            for bx in range(3):
                crop = edge.crop(
                    (
                        bx * width // 3,
                        by * height // 3,
                        (bx + 1) * width // 3,
                        (by + 1) * height // 3,
                    )
                )
                values.append(ImageStat.Stat(crop).mean[0])
        total = sum(values) or 1.0
        return [value / total for value in values]

    distance = sum(abs(a - b) for a, b in zip(bins(ref), bins(cand)))
    return clamp(100.0 * (1.0 - distance / 1.3))


def export_floor(image: Image.Image) -> float:
    gray = ImageOps.grayscale(image)
    width, height = image.size
    bands = [
        gray.crop((0, 0, width, 2)),
        gray.crop((0, height - 2, width, height)),
        gray.crop((0, 0, 2, height)),
        gray.crop((width - 2, 0, width, height)),
    ]
    dark = 0
    total = 0
    for band in bands:
        values = list(band.getdata())
        dark += sum(1 for pixel in values if pixel < 245)
        total += len(values)
    return 70.0 if dark / max(1, total) > 0.08 else 100.0


def score_candidate(
    ref: Image.Image,
    cand: Image.Image,
    strict_primary_floor: float = 0.0,
) -> dict[str, float]:
    scores = {
        "pixel_similarity": pixel_similarity(ref, cand),
        "edge_structure": edge_structure(ref, cand),
        "neutral_structure": neutral_structure(ref, cand),
        "silhouette_iou": silhouette_iou(ref, cand),
        "composition_center": composition_center(ref, cand),
        "composition_scale": composition_scale(ref, cand),
        "primary_geometry_iou": primary_geometry_iou(ref, cand),
        "primary_geometry_center": primary_geometry_center(ref, cand),
        "primary_geometry_scale": primary_geometry_scale(ref, cand),
        "primary_geometry_layout": primary_geometry_layout(ref, cand),
        "foreground_occupancy": foreground_occupancy(ref, cand),
        "palette_overlap": palette_overlap(ref, cand),
        "gradient_layout": gradient_layout(ref, cand),
        "export_floor": export_floor(cand),
    }
    scores["three_d_proxy"] = (
        0.22 * scores["edge_structure"]
        + 0.18 * scores["neutral_structure"]
        + 0.18 * scores["silhouette_iou"]
        + 0.18 * scores["primary_geometry_iou"]
        + 0.14 * scores["primary_geometry_layout"]
        + 0.10 * scores["primary_geometry_scale"]
    )
    scores["overall"] = sum(scores[key] * WEIGHTS[key] for key in WEIGHTS) / WEIGHT_TOTAL
    scores["three_d_scorecard_proxy"] = {
        "topology": min(
            100.0,
            0.50 * scores["silhouette_iou"] + 0.50 * scores["edge_structure"],
        ),
        "geometry_footprint": (
            0.35 * scores["primary_geometry_iou"]
            + 0.30 * scores["primary_geometry_layout"]
            + 0.20 * scores["primary_geometry_scale"]
            + 0.15 * scores["silhouette_iou"]
        ),
        "camera_box_aspect": (
            0.40 * scores["composition_scale"]
            + 0.35 * scores["neutral_structure"]
            + 0.25 * scores["gradient_layout"]
        ),
        "composition_occupancy": (
            0.35 * scores["composition_center"]
            + 0.35 * scores["composition_scale"]
            + 0.30 * scores["foreground_occupancy"]
        ),
        "surface_or_mark_style": (
            0.35 * scores["edge_structure"]
            + 0.30 * scores["primary_geometry_iou"]
            + 0.20 * scores["primary_geometry_layout"]
            + 0.15 * scores["palette_overlap"]
        ),
        "color_semantics": scores["palette_overlap"],
        "text_export_floor": scores["export_floor"],
    }
    scores["three_d_scorecard_proxy"]["overall"] = sum(
        scores["three_d_scorecard_proxy"][key] * weight
        for key, weight in SCORECARD_PROXY_WEIGHTS.items()
    )
    rounded = {
        key: round(value, 2)
        for key, value in scores.items()
        if key != "three_d_scorecard_proxy"
    }
    rounded["three_d_scorecard_proxy"] = {
        key: round(value, 2) for key, value in scores["three_d_scorecard_proxy"].items()
    }
    ranked_dimensions = sorted(WEIGHTS, key=lambda key: rounded[key])
    rounded["weakest_dimensions"] = [
        {"dimension": key, "score": rounded[key]} for key in ranked_dimensions[:3]
    ]
    proxy = rounded["three_d_scorecard_proxy"]
    rounded["selection_flags"] = [
        flag
        for flag, active in [
            ("low_overall", rounded["overall"] < 80.0),
            ("low_three_d_proxy", rounded["three_d_proxy"] < 70.0),
            ("low_scorecard_proxy_topology", proxy["topology"] < 75.0),
            ("low_scorecard_proxy_overall", proxy["overall"] < 80.0),
            ("low_silhouette", rounded["silhouette_iou"] < 68.0),
            ("low_composition_scale", rounded["composition_scale"] < 80.0),
            ("low_primary_geometry", proxy["geometry_footprint"] < 75.0),
            ("low_camera_box_aspect", proxy["camera_box_aspect"] < 75.0),
            ("low_composition_occupancy", proxy["composition_occupancy"] < 75.0),
            ("export_floor_risk", rounded["export_floor"] < 100.0),
            (
                "strict_primary_floor_topology",
                strict_primary_floor > 0.0 and proxy["topology"] < strict_primary_floor,
            ),
            (
                "strict_primary_floor_geometry",
                strict_primary_floor > 0.0
                and proxy["geometry_footprint"] < strict_primary_floor,
            ),
            (
                "strict_primary_floor_camera",
                strict_primary_floor > 0.0
                and proxy["camera_box_aspect"] < strict_primary_floor,
            ),
        ]
        if active
    ]
    return rounded


def add_baseline_deltas(
    candidates: list[dict[str, object]], baseline_role: str, suffix: str
) -> None:
    baseline = next(
        (item for item in candidates if item.get("role") == baseline_role),
        None,
    )
    if baseline is None:
        return
    critical_keys = (
        "silhouette_iou",
        "composition_center",
        "composition_scale",
        "primary_geometry_iou",
        "primary_geometry_center",
        "primary_geometry_scale",
        "primary_geometry_layout",
        "foreground_occupancy",
        "neutral_structure",
        "export_floor",
        "three_d_proxy",
    )
    for item in candidates:
        item[f"overall_delta_vs_{suffix}"] = round(
            float(item["overall"]) - float(baseline["overall"]),
            2,
        )
        item[f"critical_delta_vs_{suffix}"] = {
            key: round(float(item[key]) - float(baseline[key]), 2) for key in critical_keys
        }
        if item is baseline:
            continue
        flags = item["selection_flags"]
        if item[f"overall_delta_vs_{suffix}"] < 0:
            flags.append(f"worse_than_{suffix}")
        elif item[f"overall_delta_vs_{suffix}"] < 1.0:
            flags.append(f"weak_gain_vs_{suffix}")
        if any(value < -2.0 for value in item[f"critical_delta_vs_{suffix}"].values()):
            flags.append(f"critical_geometry_regression_vs_{suffix}")
        if item[f"critical_delta_vs_{suffix}"]["foreground_occupancy"] < -5.0:
            flags.append(f"subject_occupancy_regression_vs_{suffix}")
        if item[f"critical_delta_vs_{suffix}"]["export_floor"] < 0.0:
            flags.append(f"export_floor_regression_vs_{suffix}")


def add_conservative_deltas(candidates: list[dict[str, object]]) -> None:
    add_baseline_deltas(candidates, "conservative_l1", "conservative")


def add_base_path_control_deltas(candidates: list[dict[str, object]]) -> None:
    add_baseline_deltas(candidates, "base_path_control", "base_path_control")


def add_base_path_control_target_flags(candidates: list[dict[str, object]]) -> None:
    baseline = next(
        (item for item in candidates if item.get("role") == "base_path_control"),
        None,
    )
    if baseline is None:
        return
    baseline_proxy = baseline["three_d_scorecard_proxy"]
    for item in candidates:
        role = item.get("role")
        targets = ROLE_TARGET_SCORECARD_DIMENSIONS.get(str(role))
        if not targets:
            continue
        proxy = item["three_d_scorecard_proxy"]
        target_delta = {
            key: round(float(proxy[key]) - float(baseline_proxy[key]), 2)
            for key in targets
        }
        preserve_delta = {
            key: round(float(proxy[key]) - float(baseline_proxy[key]), 2)
            for key in PRIMARY_SCORECARD_DIMENSIONS
            if key not in targets
        }
        item["target_scorecard_delta_vs_base_path_control"] = target_delta
        item["primary_scorecard_preservation_delta_vs_base_path_control"] = (
            preserve_delta
        )
        flags = item["selection_flags"]
        if not any(value >= 1.0 for value in target_delta.values()):
            flags.append("no_target_scorecard_gain_vs_base_path_control")
        if any(value < -1.0 for value in target_delta.values()):
            flags.append("target_scorecard_regression_vs_base_path_control")
        if any(value < -1.0 for value in preserve_delta.values()):
            flags.append("primary_scorecard_regression_vs_base_path_control")


def add_role_repair_flags(candidates: list[dict[str, object]]) -> None:
    for item in candidates:
        role = item.get("role")
        proxy = item["three_d_scorecard_proxy"]
        flags = item["selection_flags"]
        if role == "topology_repair" and proxy["topology"] < 75.0:
            flags.append("proxy_topology_repair_incomplete")
        if role == "aspect_or_camera_repair" and proxy["camera_box_aspect"] < 75.0:
            flags.append("proxy_camera_aspect_repair_incomplete")
        if role == "path_footprint_repair" and (
            proxy["topology"] < 75.0 or proxy["geometry_footprint"] < 75.0
        ):
            flags.append("proxy_path_footprint_repair_incomplete")
        if role == "scatter_hull_repair" and (
            proxy["topology"] < 82.0
            or proxy["geometry_footprint"] < 82.0
        ):
            flags.append("proxy_scatter_hull_repair_incomplete")
        if role == "trajectory_camera_probe" and (
            proxy["topology"] < 82.0
            or proxy["camera_box_aspect"] < 82.0
            or proxy["geometry_footprint"] < 82.0
        ):
            flags.append("proxy_trajectory_camera_probe_incomplete")
        if role == "camera_register_probe" and (
            proxy["topology"] < 82.0
            or proxy["camera_box_aspect"] < 82.0
            or proxy["geometry_footprint"] < 82.0
            or proxy["composition_occupancy"] < 82.0
        ):
            flags.append("proxy_camera_register_probe_incomplete")
        if role == "layout_containment" and (
            proxy["composition_occupancy"] < 75.0 or proxy["text_export_floor"] < 100.0
        ):
            flags.append("proxy_layout_repair_incomplete")


def proxy_selection_blockers(best: dict[str, object] | None) -> list[str]:
    if best is None:
        return ["no_best_candidate"]
    blocker_flags = {
        "low_primary_geometry",
        "low_camera_box_aspect",
        "low_composition_occupancy",
        "low_scorecard_proxy_topology",
        "strict_primary_floor_topology",
        "strict_primary_floor_geometry",
        "strict_primary_floor_camera",
        "export_floor_risk",
        "critical_geometry_regression_vs_conservative",
        "subject_occupancy_regression_vs_conservative",
        "export_floor_regression_vs_conservative",
        "worse_than_base_path_control",
        "weak_gain_vs_base_path_control",
        "critical_geometry_regression_vs_base_path_control",
        "subject_occupancy_regression_vs_base_path_control",
        "export_floor_regression_vs_base_path_control",
        "no_target_scorecard_gain_vs_base_path_control",
        "target_scorecard_regression_vs_base_path_control",
        "primary_scorecard_regression_vs_base_path_control",
        "proxy_topology_repair_incomplete",
        "proxy_camera_aspect_repair_incomplete",
        "proxy_path_footprint_repair_incomplete",
        "proxy_scatter_hull_repair_incomplete",
        "proxy_trajectory_camera_probe_incomplete",
        "proxy_camera_register_probe_incomplete",
        "proxy_layout_repair_incomplete",
    }
    flags = [flag for flag in best["selection_flags"] if flag in blocker_flags]
    proxy = best["three_d_scorecard_proxy"]
    if proxy["overall"] < 80.0:
        flags.append("low_scorecard_proxy_overall")
    return sorted(set(flags))


def main() -> int:
    args = parse_args()
    canvas = (args.canvas, args.canvas)
    ref = load_image(args.reference, canvas)
    roles = resolve_roles(len(args.candidate), args.role)
    candidates = []
    for index, path in enumerate(args.candidate):
        scores = score_candidate(ref, load_image(path, canvas), args.strict_primary_floor)
        candidates.append({"id": f"candidate_{index}", "role": roles[index], **scores})
    add_conservative_deltas(candidates)
    add_base_path_control_deltas(candidates)
    add_base_path_control_target_flags(candidates)
    add_role_repair_flags(candidates)
    candidates.sort(key=lambda item: item["overall"], reverse=True)
    roles_seen = sorted(role for role in set(roles) if role != "unspecified")
    result_flags = []
    if any(role == "unspecified" for role in roles):
        result_flags.append("missing_role_metadata")
    if args.role is not None:
        for role in REQUIRED_ROLES:
            if role not in roles_seen:
                result_flags.append(f"missing_{role}")
    candidate_sweep_valid = not any(flag.startswith("missing_") for flag in result_flags)
    if args.allow_unspecified_roles and result_flags == ["missing_role_metadata"]:
        candidate_sweep_valid = True
        result_flags.append("diagnostic_only_unspecified_roles")
    best = candidates[0] if candidates and candidate_sweep_valid else None
    blockers = proxy_selection_blockers(best)
    if args.allow_unspecified_roles:
        blockers.append("diagnostic_only")
    result = {
        "reference": "reference",
        "diagnostic_only": bool(args.allow_unspecified_roles),
        "strict_primary_floor": args.strict_primary_floor,
        "weights": WEIGHTS,
        "scorecard_proxy_weights": SCORECARD_PROXY_WEIGHTS,
        "required_roles": list(REQUIRED_ROLES),
        "roles_seen": roles_seen,
        "candidate_sweep_valid": candidate_sweep_valid,
        "proxy_selection_ready": candidate_sweep_valid and not blockers,
        "proxy_selection_blockers": sorted(set(blockers)),
        "non_conservative_finalization_requires_external_audit": bool(
            best
            and blockers
            and best.get("role") not in {"base_path_control", "conservative_l1"}
        ),
        "non_control_finalization_requires_external_audit": bool(
            best
            and blockers
            and best.get("role") not in {"base_path_control", "conservative_l1"}
        ),
        "selection_flags": result_flags,
        "best": best,
        "candidates": candidates,
    }
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
