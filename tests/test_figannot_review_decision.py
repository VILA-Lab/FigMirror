from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from PIL import Image


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".codex"
    / "skills"
    / "figmirror"
    / "scripts"
    / "figannot.py"
)
SPEC = importlib.util.spec_from_file_location("figannot_review", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
figannot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(figannot)


def _clean_review() -> dict:
    return {
        "iter": 0,
        "reference_inventory": {
            "chart_type": "single-panel line chart",
            "signature_element": "paired lines with a compact legend",
            "motifs": ["muted palette", "thin spines"],
        },
        "anchor": {
            "what_is_right": ["[L1] Panel geometry matches the reference."],
            "measurements": {},
        },
        "quality_floor": {
            "passed": True,
            "violation_kinds": [],
            "summary": None,
        },
        "fidelity": {"verdict": "ship", "paragraph": "Ready to ship."},
        "focus_themes": [],
        "boxes": [],
    }


def _scorecard(*, overall: int = 90, topology: int = 86) -> dict:
    return {
        "topology": topology,
        "geometry_footprint": 86,
        "camera_box_aspect": 86,
        "composition_occupancy": 86,
        "surface_or_mark_style": 84,
        "color_semantics": 84,
        "text_export_floor": 84,
        "overall": overall,
        "summary": "All strict reproduction gates pass.",
    }


def test_strict_3d_ship_requires_scorecard():
    classification, reason = figannot.classify_review(
        _clean_review(), strict_3d=True
    )
    assert classification == "invalid"
    assert "three_d_scorecard" in reason


def test_base_review_requires_preserve_schema():
    review = _clean_review()
    del review["anchor"]
    classification, reason = figannot.classify_review(review)
    assert classification == "invalid"
    assert reason == "anchor must be an object"


def test_base_review_requires_reference_inventory():
    review = _clean_review()
    del review["reference_inventory"]
    classification, reason = figannot.classify_review(review)
    assert classification == "invalid"
    assert reason == "reference_inventory must be an object"


def test_base_review_requires_fidelity_paragraph():
    review = _clean_review()
    del review["fidelity"]["paragraph"]
    classification, reason = figannot.classify_review(review)
    assert classification == "invalid"
    assert reason == "fidelity.paragraph must be a non-empty string"


def test_strict_3d_ship_enforces_thresholds():
    review = _clean_review()
    review["three_d_scorecard"] = _scorecard(overall=84)
    classification, reason = figannot.classify_review(review, strict_3d=True)
    assert classification == "invalid"
    assert "overall >= 85" in reason


def test_strict_3d_ship_with_passing_scorecard_is_all_clear():
    review = _clean_review()
    review["three_d_scorecard"] = _scorecard()
    assert figannot.classify_review(review, strict_3d=True)[0] == "all_clear"


def test_review_decision_records_strict_mode(tmp_path, capsys):
    Image.new("RGB", (8, 8), "white").save(tmp_path / "img_iter0.png")
    review = _clean_review()
    review["three_d_scorecard"] = _scorecard()
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")

    figannot.cmd_review_decision(argparse.Namespace(
        workdir=str(tmp_path),
        review=str(review_path),
        draft=str(tmp_path / "img_iter0.png"),
        iter="0",
        min_reviews=1,
        max_iters=5,
        strict_3d=True,
        reviewer_session="reviewer-1",
    ))

    result = json.loads(capsys.readouterr().out)
    attempt = json.loads(
        (tmp_path / "review_attempts" / "attempt_000.json").read_text()
    )
    assert result["action"] == "ship"
    assert attempt["strict_3d"] is True
