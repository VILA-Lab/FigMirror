#!/usr/bin/env python3
"""FigMirror annotation operator: deterministic compose, review gate, and draw.

The Orchestrator calls this script; the model does not hand-write annotation
code. It builds a normalized far-view composite, then draws Reviewer-returned
boxes into an annotated image and notes file for the next Drawer invocation.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
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

_ALLOWED_VIOLATION_KINDS = {
    "text_overlaps_tick",
    "text_overlaps_title",
    "text_overlaps_text_in_axes",
    "text_obscured_by_marks",
    "label_clipped",
    "axis_drawn_off_canvas",
    "illegible_at_print_size",
    "default_matplotlib_aesthetic",
    "font_family_mismatch",
    "font_weight_too_heavy",
    "chart_type_abandoned",
    "signature_motif_dropped",
    "encoding_oversimplified",
}

_STRICT_3D_SCORE_FIELDS = (
    "topology",
    "geometry_footprint",
    "camera_box_aspect",
    "composition_occupancy",
    "surface_or_mark_style",
    "color_semantics",
    "text_export_floor",
    "overall",
)
_STRICT_3D_PRIMARY_MINIMUMS = {
    "topology": 82,
    "geometry_footprint": 82,
    "camera_box_aspect": 82,
    "composition_occupancy": 82,
}


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
    with Image.open(args.ref) as source_ref:
        ref = source_ref.convert("RGB")
    with Image.open(args.draft) as source_draft:
        draft = source_draft.convert("RGB")
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

    (out / "review_prompt.txt").write_text(base + dims + diag, encoding="utf-8")
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


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        Path(tmp_name).replace(path)
    except Exception:
        try:
            Path(tmp_name).unlink()
        except FileNotFoundError:
            pass
        raise


def _nonempty_strings(value) -> list[str] | None:
    if not isinstance(value, list):
        return None
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        text = item.strip()
        if text:
            items.append(text)
    return items


def _valid_action_boxes(value) -> list[dict] | None:
    if not isinstance(value, list):
        return None
    valid: list[dict] = []
    for box in value:
        if not isinstance(box, dict):
            return None
        coordinates = [box.get(key) for key in ("x0", "y0", "x1", "y1")]
        if any(
            isinstance(coordinate, bool) or not isinstance(coordinate, int)
            for coordinate in coordinates
        ):
            return None
        x0, y0, x1, y1 = coordinates
        if x0 >= x1 or y0 >= y1 or not str(box.get("note") or "").strip():
            return None
        valid.append(box)
    return valid


def _strict_3d_scorecard_error(review: dict, *, require_pass: bool) -> str | None:
    scorecard = review.get("three_d_scorecard")
    if not isinstance(scorecard, dict):
        return "strict 3D review must include three_d_scorecard"
    scores: dict[str, int] = {}
    for field in _STRICT_3D_SCORE_FIELDS:
        value = scorecard.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            return f"three_d_scorecard.{field} must be an integer from 0 to 100"
        scores[field] = value
    if not isinstance(scorecard.get("summary"), str) or not scorecard["summary"].strip():
        return "three_d_scorecard.summary must be a non-empty string"
    if not require_pass:
        return None
    if scores["overall"] < 85:
        return "strict 3D ship requires three_d_scorecard.overall >= 85"
    below_floor = [field for field, value in scores.items() if value < 75]
    if below_floor:
        return "strict 3D ship has a score below 75: " + ", ".join(below_floor)
    below_primary = [
        field
        for field, minimum in _STRICT_3D_PRIMARY_MINIMUMS.items()
        if scores[field] < minimum
    ]
    if below_primary:
        return "strict 3D ship has a primary score below 82: " + ", ".join(
            below_primary
        )
    return None


def _review_schema_error(review: dict) -> str | None:
    inventory = review.get("reference_inventory")
    if not isinstance(inventory, dict):
        return "reference_inventory must be an object"
    for field in ("chart_type", "signature_element"):
        value = inventory.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"reference_inventory.{field} must be a non-empty string"
    motifs = _nonempty_strings(inventory.get("motifs"))
    if motifs is None or not motifs:
        return "reference_inventory.motifs must be a non-empty string list"

    anchor = review.get("anchor")
    if not isinstance(anchor, dict):
        return "anchor must be an object"
    what_is_right = _nonempty_strings(anchor.get("what_is_right"))
    if what_is_right is None or not what_is_right:
        return "anchor.what_is_right must be a non-empty string list"
    measurements = anchor.get("measurements")
    if measurements is not None and not isinstance(measurements, dict):
        return "anchor.measurements must be an object when present"

    fidelity = review.get("fidelity")
    paragraph = fidelity.get("paragraph") if isinstance(fidelity, dict) else None
    if not isinstance(paragraph, str) or not paragraph.strip():
        return "fidelity.paragraph must be a non-empty string"
    return None


def classify_review(review: object, *, strict_3d: bool = False) -> tuple[str, str]:
    """Classify a Reviewer result without asking the Orchestrator to interpret it."""
    if not isinstance(review, dict):
        return "invalid", "review must be a JSON object"
    floor = review.get("quality_floor")
    fidelity = review.get("fidelity")
    if not isinstance(floor, dict) or not isinstance(fidelity, dict):
        return "invalid", "quality_floor and fidelity must be objects"
    schema_error = _review_schema_error(review)
    if schema_error is not None:
        return "invalid", schema_error

    passed = floor.get("passed")
    if not isinstance(passed, bool):
        return "invalid", "quality_floor.passed must be boolean"
    raw_kinds = floor.get("violation_kinds")
    if not isinstance(raw_kinds, list) or any(not isinstance(item, str) for item in raw_kinds):
        return "invalid", "quality_floor.violation_kinds must be a string list"
    kinds = [item.strip() for item in raw_kinds if item.strip()]
    unknown = sorted(set(kinds) - _ALLOWED_VIOLATION_KINDS)
    if unknown:
        return "invalid", f"unknown quality-floor violation kinds: {', '.join(unknown)}"

    summary_raw = floor.get("summary")
    if summary_raw is not None and not isinstance(summary_raw, str):
        return "invalid", "quality_floor.summary must be a string or null"
    summary = str(summary_raw or "").strip()
    verdict = fidelity.get("verdict")
    if verdict not in {"ship", "close", "off"}:
        return "invalid", "fidelity.verdict must be ship, close, or off"

    themes = _nonempty_strings(review.get("focus_themes"))
    boxes = _valid_action_boxes(review.get("boxes"))
    if themes is None or boxes is None:
        return "invalid", "focus_themes and boxes must be valid lists"

    if strict_3d:
        strict_error = _strict_3d_scorecard_error(
            review,
            require_pass=(
                passed
                and not kinds
                and not summary
                and verdict == "ship"
                and not themes
                and not boxes
            ),
        )
        if strict_error is not None:
            return "invalid", strict_error

    if passed and not kinds and not summary and verdict == "ship" and not themes and not boxes:
        return "all_clear", "quality floor passed and Reviewer supplied no repair"

    if passed:
        if kinds:
            return "invalid", "passed quality floor cannot list violations"
        if summary:
            return "invalid", "passed quality floor must use an empty summary"
        if verdict in {"close", "off"} and (themes or boxes):
            return "actionable", "non-shipping verdict includes a concrete repair"
        return "invalid", "shipping and feedback fields are inconsistent"

    if verdict == "ship":
        return "invalid", "ship cannot accompany a failed quality floor"
    if not kinds:
        return "invalid", "failed quality floor must name a violation kind"
    if not summary:
        return "invalid", "failed quality floor must include a summary"
    return "actionable", "failed quality floor names a concrete violation"


def _is_nonnegative_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _canonical_payload_sha256(payload: object) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _review_result_id(
    *,
    reviewer_session: str,
    iter_idx: int,
    draft_sha256: str,
    terminal_payload_sha256: str,
) -> str:
    identity = {
        "reviewer_session": reviewer_session,
        "iter": iter_idx,
        "draft_sha256": draft_sha256,
        "terminal_payload_sha256": terminal_payload_sha256,
    }
    return _canonical_payload_sha256(identity)


def _review_attempts(workdir: Path) -> list[dict]:
    attempts_dir = workdir / "review_attempts"
    attempts: list[dict] = []
    valid_count = 0
    configured_min_reviews: int | None = None
    configured_max_iters: int | None = None
    configured_strict_3d: bool | None = None
    previous_valid: dict | None = None
    invalid_retry_pending = False
    seen_result_ids: set[str] = set()
    for expected_index, path in enumerate(sorted(attempts_dir.glob("attempt_*.json"))):
        if path.name != f"attempt_{expected_index:03d}.json":
            raise ValueError(f"non-contiguous review attempt ledger: {path}")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"invalid review attempt ledger entry: {path}")
        if loaded.get("state") != "committed":
            raise ValueError(f"uncommitted review attempt ledger entry: {path}")
        recorded_attempt = loaded.get("attempt")
        if (
            not _is_nonnegative_int(recorded_attempt)
            or recorded_attempt != expected_index
        ):
            raise ValueError(f"invalid review attempt index in ledger entry: {path}")

        iter_idx = loaded.get("iter")
        if isinstance(iter_idx, bool) or not isinstance(iter_idx, int) or iter_idx < 0:
            raise ValueError(f"invalid review iteration in ledger entry: {path}")
        max_iters = loaded.get("max_iters")
        if (
            isinstance(max_iters, bool)
            or not isinstance(max_iters, int)
            or max_iters < 1
        ):
            raise ValueError(f"invalid max_iters in ledger entry: {path}")
        if iter_idx >= max_iters:
            raise ValueError(f"review iteration exceeds max_iters in ledger entry: {path}")
        if configured_max_iters is None:
            configured_max_iters = max_iters
        elif max_iters != configured_max_iters:
            raise ValueError(f"max_iters changed within review ledger: {path}")
        strict_3d = loaded.get("strict_3d", False)
        if not isinstance(strict_3d, bool):
            raise ValueError(f"invalid strict_3d flag in ledger entry: {path}")
        if configured_strict_3d is None:
            configured_strict_3d = strict_3d
        elif strict_3d != configured_strict_3d:
            raise ValueError(f"strict_3d changed within review ledger: {path}")
        reviewer_session = loaded.get("reviewer_session")
        if not isinstance(reviewer_session, str) or not reviewer_session.strip():
            raise ValueError(f"missing Reviewer session in ledger entry: {path}")

        classification = loaded.get("classification")
        if classification not in {"invalid", "all_clear", "actionable"}:
            raise ValueError(
                f"unknown review classification {classification!r} in ledger entry: {path}"
            )
        if "review" not in loaded:
            raise ValueError(f"missing review payload in ledger entry: {path}")
        review = loaded["review"]
        terminal_payload_sha256 = loaded.get("terminal_payload_sha256")
        if (
            not isinstance(terminal_payload_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", terminal_payload_sha256) is None
            or _canonical_payload_sha256(review) != terminal_payload_sha256
        ):
            raise ValueError(f"invalid terminal payload hash in ledger entry: {path}")
        if classification in {"all_clear", "actionable"}:
            if not isinstance(review, dict):
                raise ValueError(f"missing review object in ledger entry: {path}")
            recomputed, _ = classify_review(review, strict_3d=strict_3d)
            review_iter = review.get("iter")
            if (
                recomputed != classification
                or not _is_nonnegative_int(review_iter)
                or review_iter != iter_idx
            ):
                raise ValueError(f"review classification mismatch in ledger entry: {path}")
            valid_count += 1

        min_reviews = loaded.get("min_reviews")
        if (
            isinstance(min_reviews, bool)
            or not isinstance(min_reviews, int)
            or min_reviews < 1
        ):
            raise ValueError(f"invalid min_reviews in ledger entry: {path}")
        if configured_min_reviews is None:
            configured_min_reviews = min_reviews
        elif min_reviews != configured_min_reviews:
            raise ValueError(f"min_reviews changed within review ledger: {path}")
        recorded_valid_count = loaded.get("valid_review_count")
        if (
            not _is_nonnegative_int(recorded_valid_count)
            or recorded_valid_count != valid_count
        ):
            raise ValueError(f"invalid valid_review_count in ledger entry: {path}")

        draft = loaded.get("draft")
        canonical_draft = (workdir / f"img_iter{iter_idx}.png").resolve()
        if not isinstance(draft, str) or Path(draft).resolve() != canonical_draft:
            raise ValueError(f"invalid draft path in ledger entry: {path}")
        draft_sha = loaded.get("draft_sha256")
        if (
            not isinstance(draft_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", draft_sha) is None
            or not canonical_draft.is_file()
            or _sha256(canonical_draft) != draft_sha
        ):
            raise ValueError(f"invalid draft hash in ledger entry: {path}")

        result_id = loaded.get("result_id")
        if (
            not isinstance(result_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", result_id) is None
            or result_id
            != _review_result_id(
                reviewer_session=reviewer_session,
                iter_idx=iter_idx,
                draft_sha256=draft_sha,
                terminal_payload_sha256=terminal_payload_sha256,
            )
        ):
            raise ValueError(f"invalid result_id in ledger entry: {path}")
        if result_id in seen_result_ids:
            raise ValueError(f"duplicate result_id in review ledger entry: {path}")
        seen_result_ids.add(result_id)

        expected_action = {
            "invalid": "retry_reviewer",
            "actionable": "stop_at_cap" if iter_idx == max_iters - 1 else "draw",
            "all_clear": (
                "review_same_draft"
                if valid_count < min_reviews
                else "ship"
            ),
        }[classification]
        if loaded.get("action") != expected_action:
            raise ValueError(f"invalid review action in ledger entry: {path}")

        if classification == "invalid":
            if invalid_retry_pending:
                raise ValueError(
                    "review retry is exhausted after consecutive invalid attempts "
                    f"in ledger entry: {path}"
                )
            invalid_retry_pending = True
        else:
            invalid_retry_pending = False

        if previous_valid and previous_valid.get("action") in {"ship", "stop_at_cap"}:
            raise ValueError(f"review ledger continues after terminal action: {path}")
        if classification in {"all_clear", "actionable"} and previous_valid:
            previous_action = previous_valid.get("action")
            previous_iter = int(previous_valid["iter"])
            previous_sha = str(previous_valid["draft_sha256"])
            if previous_action == "review_same_draft" and (
                iter_idx != previous_iter or draft_sha != previous_sha
            ):
                raise ValueError(
                    f"review_same_draft transition changed the draft in ledger entry: {path}"
                )
            if previous_action == "draw" and (
                iter_idx != previous_iter + 1 or draft_sha == previous_sha
            ):
                raise ValueError(
                    f"draw transition did not produce a changed next iteration in ledger entry: {path}"
                )
        if classification in {"all_clear", "actionable"}:
            previous_valid = loaded
        attempts.append(loaded)
    return attempts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_canonical_review(
    workdir: Path, *, iter_idx: int, action: str, review: object
) -> None:
    if action not in {"draw", "ship", "stop_at_cap"}:
        return
    canonical_review = review
    if action == "stop_at_cap" and isinstance(review, dict):
        audit_view = workdir / f"audit_view_{iter_idx}"
        with Image.open(audit_view / "composite.png") as image:
            meta = _read_meta(audit_view, image)
        canonical_review, _ = _normalize_review_payload(review, meta)
    review_text = json.dumps(canonical_review, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(workdir / f"audit_iter{iter_idx}.json", review_text)
    _atomic_write_text(
        workdir / f"review_feedback_{iter_idx}" / "review.json", review_text
    )


def _decision_result(attempt: dict, *, replayed: bool) -> dict:
    result = {key: value for key, value in attempt.items() if key != "review"}
    result["replayed"] = replayed
    return result


def _emit_decision_result(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False))


def _review_transaction_lock(workdir: Path):
    attempts_dir = workdir / "review_attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    handle = (attempts_dir / ".transaction.lock").open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _commit_review_decision(
    *,
    workdir: Path,
    review: object,
    draft: Path,
    iter_idx: int,
    min_reviews: int,
    max_iters: int,
    strict_3d: bool,
    reviewer_session: str,
    classification: str,
    reason: str,
) -> None:
    attempts = _review_attempts(workdir)
    draft_sha = _sha256(draft)
    terminal_payload_sha256 = _canonical_payload_sha256(review)
    result_id = _review_result_id(
        reviewer_session=reviewer_session,
        iter_idx=iter_idx,
        draft_sha256=draft_sha,
        terminal_payload_sha256=terminal_payload_sha256,
    )

    if attempts and attempts[0].get("min_reviews") != min_reviews:
        raise ValueError("min_reviews does not match the existing review ledger")
    if attempts and attempts[0].get("max_iters") != max_iters:
        raise ValueError("max_iters does not match the existing review ledger")
    if attempts and bool(attempts[0].get("strict_3d", False)) != strict_3d:
        raise ValueError("strict_3d does not match the existing review ledger")

    replay_index = next(
        (
            index
            for index, item in enumerate(attempts)
            if item.get("result_id") == result_id
        ),
        None,
    )
    replay = attempts[replay_index] if replay_index is not None else None
    if replay is not None:
        if replay_index != len(attempts) - 1:
            raise ValueError(
                "stale review result replay was superseded by a later attempt"
            )
        _emit_decision_result(_decision_result(replay, replayed=True))
        return

    if attempts and attempts[-1].get("action") in {"ship", "stop_at_cap"}:
        raise ValueError("review ledger is already terminal")

    if reviewer_session in {str(item.get("reviewer_session") or "") for item in attempts}:
        classification = "invalid"
        reason = "reviewer session was reused; an independent fresh Reviewer is required"

    valid_attempts = [
        item
        for item in attempts
        if item.get("classification") in {"all_clear", "actionable"}
    ]
    if classification != "invalid" and valid_attempts:
        previous = valid_attempts[-1]
        previous_action = previous.get("action")
        previous_iter = int(previous["iter"])
        previous_sha = str(previous.get("draft_sha256") or "")
        if previous_action == "review_same_draft" and (
            iter_idx != previous_iter or draft_sha != previous_sha
        ):
            classification = "invalid"
            reason = "draft changed before the required same-draft confirmation review"
        elif previous_action == "draw" and iter_idx != previous_iter + 1:
            classification = "invalid"
            reason = (
                f"actionable feedback requires Drawer iteration {previous_iter + 1} "
                "before another valid review"
            )
        elif previous_action == "draw" and draft_sha == previous_sha:
            classification = "invalid"
            reason = "actionable feedback requires the Drawer to produce a changed draft"

    valid_review_count = len(valid_attempts) + (classification != "invalid")
    if classification == "invalid":
        action = "retry_reviewer"
    elif classification == "actionable":
        if iter_idx == max_iters - 1:
            action = "stop_at_cap"
        else:
            action = "draw"
    elif valid_review_count < min_reviews:
        action = "review_same_draft"
    else:
        action = "ship"

    attempt = {
        "state": "committed",
        "attempt": len(attempts),
        "iter": iter_idx,
        "reviewer_session": reviewer_session,
        "draft": str(draft.resolve()),
        "draft_sha256": draft_sha,
        "terminal_payload_sha256": terminal_payload_sha256,
        "result_id": result_id,
        "classification": classification,
        "action": action,
        "reason": reason,
        "valid_review_count": valid_review_count,
        "min_reviews": min_reviews,
        "max_iters": max_iters,
        "strict_3d": strict_3d,
        "review": review,
    }
    attempt_path = workdir / "review_attempts" / f"attempt_{len(attempts):03d}.json"
    _write_canonical_review(
        workdir,
        iter_idx=iter_idx,
        action=action,
        review=review,
    )
    _atomic_write_text(
        attempt_path,
        json.dumps(attempt, ensure_ascii=False, indent=2) + "\n",
    )

    if (
        classification == "invalid"
        and attempts
        and attempts[-1].get("classification") == "invalid"
    ):
        raise ValueError(
            "second invalid Reviewer result for the same review slot; fail closed"
        )

    _emit_decision_result(_decision_result(attempt, replayed=False))


def cmd_review_decision(args) -> None:
    workdir = Path(args.workdir)
    review_path = Path(args.review)
    draft = Path(args.draft)
    iter_idx = int(args.iter)
    if iter_idx < 0:
        raise ValueError("review iteration must be non-negative")
    min_reviews = int(args.min_reviews)
    if min_reviews < 1:
        raise ValueError("min_reviews must be at least 1")
    max_iters = getattr(args, "max_iters", 5)
    if (
        isinstance(max_iters, bool)
        or not isinstance(max_iters, int)
        or max_iters < 1
    ):
        raise ValueError("max_iters must be an integer of at least 1")
    if iter_idx >= max_iters:
        raise ValueError(
            f"review iteration {iter_idx} is outside Drawer range 0..{max_iters - 1}"
        )
    reviewer_session = str(args.reviewer_session).strip()
    if not reviewer_session:
        raise ValueError("reviewer session id must be non-empty")
    if not draft.is_file():
        raise FileNotFoundError(f"draft does not exist: {draft}")
    expected_draft = (workdir / f"img_iter{iter_idx}.png").resolve()
    if draft.resolve() != expected_draft:
        raise ValueError(f"draft must be the canonical img_iter{iter_idx}.png")

    review_input_error: str | None = None
    if not review_path.is_file():
        review = None
        review_input_error = f"review output file is missing: {review_path}"
    else:
        raw_review = review_path.read_text(encoding="utf-8", errors="replace")
        try:
            review = json.loads(raw_review)
        except json.JSONDecodeError as exc:
            review = raw_review
            review_input_error = f"review output is not valid JSON: {exc}"
    strict_3d = bool(getattr(args, "strict_3d", False))
    classification, reason = classify_review(review, strict_3d=strict_3d)
    if review_input_error is not None:
        classification = "invalid"
        reason = review_input_error
    if isinstance(review, dict):
        review_iter = review.get("iter")
        if not _is_nonnegative_int(review_iter):
            classification = "invalid"
            reason = (
                "review iter must be a non-negative integer; "
                f"saw {review_iter!r}"
            )
        elif review_iter != iter_idx:
            classification = "invalid"
            reason = f"review iter {review_iter!r} does not match expected {iter_idx}"
    with _review_transaction_lock(workdir):
        _commit_review_decision(
            workdir=workdir,
            review=review,
            draft=draft,
            iter_idx=iter_idx,
            min_reviews=min_reviews,
            max_iters=max_iters,
            strict_3d=strict_3d,
            reviewer_session=reviewer_session,
            classification=classification,
            reason=reason,
        )


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


def cmd_check_drawer_bundle(args: argparse.Namespace) -> None:
    """Report whether one Drawer iteration has its complete four-file bundle."""

    workdir = Path(args.workdir).resolve()
    iter_idx = int(args.iter)
    if iter_idx < 0:
        raise ValueError("iter must be non-negative")
    expected = (
        f"figure_iter{iter_idx}.py",
        f"img_iter{iter_idx}.png",
        f"notes_iter{iter_idx}.md",
        f"floor_selfcheck_iter{iter_idx}.txt",
    )
    missing = [
        name
        for name in expected
        if not (workdir / name).is_file() or (workdir / name).stat().st_size == 0
    ]
    print(
        json.dumps(
            {"iter": iter_idx, "complete": not missing, "missing": missing},
            ensure_ascii=False,
        )
    )
    if missing:
        raise SystemExit(1)


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


def _normalize_review_payload(
    review: dict, meta: dict[str, int]
) -> tuple[dict, list[str]]:
    normalized = dict(review)
    boxes = normalized.get("boxes", [])
    if not isinstance(boxes, list):
        boxes = []
    boxes, repairs = _normalize_boxes(boxes, meta)
    normalized["boxes"] = boxes
    if repairs:
        normalized["box_coordinate_repairs"] = repairs
    return normalized, repairs


def _mirror_audit_iter(out: Path, review_text: str) -> None:
    match = re.fullmatch(r"review_feedback_(\d+)", out.name)
    if not match:
        return
    mirror = out.parent / f"audit_iter{match.group(1)}.json"
    if mirror.exists():
        _atomic_write_text(mirror, review_text)


def cmd_draw(args) -> None:
    out = Path(args.out_dir)
    match = re.fullmatch(r"review_feedback_(\d+)", out.name)
    if match is None:
        raise ValueError("draw out-dir must be review_feedback_<N>")
    iter_idx = int(match.group(1))
    if args.max_iters < 1:
        raise ValueError("max-iters must be at least 1")
    if iter_idx >= args.max_iters - 1:
        raise SystemExit(
            f"Drawer cap reached: iter {iter_idx} cannot authorize iter {iter_idx + 1} "
            f"when max_iters={args.max_iters}"
        )
    audit_view = out.parent / f"audit_view_{iter_idx}"
    review = _parse_review(out / "review.json")
    boxes = review.get("boxes", []) if isinstance(review, dict) else []
    if not isinstance(boxes, list):
        boxes = []
    with Image.open(audit_view / "composite.png") as source_image:
        image = source_image.convert("RGB")
    meta = _read_meta(audit_view, image)
    if isinstance(review, dict):
        review, repairs = _normalize_review_payload(review, meta)
        boxes = review["boxes"]
        review_text = json.dumps(review, ensure_ascii=False, indent=2) + "\n"
        _atomic_write_text(out / "review.json", review_text)
        _mirror_audit_iter(out, review_text)
    else:
        boxes, repairs = _normalize_boxes(boxes, meta)
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
    compose.add_argument("--height", type=int, default=920)
    compose.add_argument("--gutter", type=int, default=28)
    compose.set_defaults(func=cmd_compose)

    decision = sub.add_parser("review-decision")
    decision.add_argument("--review", required=True)
    decision.add_argument("--workdir", required=True)
    decision.add_argument("--iter", required=True)
    decision.add_argument("--draft", required=True)
    decision.add_argument("--reviewer-session", required=True)
    decision.add_argument("--min-reviews", type=int, default=2)
    decision.add_argument("--max-iters", type=int, default=5)
    decision.add_argument("--strict-3d", action="store_true")
    decision.set_defaults(func=cmd_review_decision)

    bundle = sub.add_parser("check-drawer-bundle")
    bundle.add_argument("--workdir", required=True)
    bundle.add_argument("--iter", required=True)
    bundle.set_defaults(func=cmd_check_drawer_bundle)

    draw = sub.add_parser("draw")
    draw.add_argument("--out-dir", required=True)
    draw.add_argument("--max-iters", type=int, required=True)
    draw.set_defaults(func=cmd_draw)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
