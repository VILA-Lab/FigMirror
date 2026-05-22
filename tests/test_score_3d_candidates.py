"""Regression tests for the bundled 3D candidate scorer."""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCORER_PATH = (
    REPO_ROOT
    / ".codex"
    / "skills"
    / "figmirror"
    / "scripts"
    / "score_3d_candidates.py"
)


def _load_scorer():
    spec = importlib.util.spec_from_file_location("score_3d_candidates", SCORER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate(
    role: str,
    *,
    topology: float = 90.0,
    geometry_footprint: float = 90.0,
    camera_box_aspect: float = 90.0,
    composition_occupancy: float = 90.0,
    text_export_floor: float = 100.0,
) -> dict[str, object]:
    return {
        "role": role,
        "selection_flags": [],
        "three_d_scorecard_proxy": {
            "topology": topology,
            "geometry_footprint": geometry_footprint,
            "camera_box_aspect": camera_box_aspect,
            "composition_occupancy": composition_occupancy,
            "surface_or_mark_style": 90.0,
            "color_semantics": 90.0,
            "text_export_floor": text_export_floor,
            "overall": 90.0,
        },
    }


def test_repair_without_target_gain_is_blocked_against_base_path_control():
    scorer = _load_scorer()
    control = _candidate("base_path_control")
    repair = _candidate(
        "camera_register_probe",
        geometry_footprint=90.5,
        camera_box_aspect=90.25,
    )

    scorer.add_base_path_control_target_flags([repair, control])

    assert repair["target_scorecard_delta_vs_base_path_control"] == {
        "camera_box_aspect": 0.25,
        "geometry_footprint": 0.5,
    }
    assert "no_target_scorecard_gain_vs_base_path_control" in repair["selection_flags"]
    assert (
        "no_target_scorecard_gain_vs_base_path_control"
        in scorer.proxy_selection_blockers(repair)
    )


def test_repair_that_regresses_primary_dimensions_is_blocked_against_control():
    scorer = _load_scorer()
    control = _candidate("base_path_control")
    repair = _candidate(
        "camera_register_probe",
        geometry_footprint=92.0,
        camera_box_aspect=92.0,
        topology=88.5,
    )

    scorer.add_base_path_control_target_flags([repair, control])

    assert repair["primary_scorecard_preservation_delta_vs_base_path_control"][
        "topology"
    ] == -1.5
    assert "primary_scorecard_regression_vs_base_path_control" in repair["selection_flags"]
    assert (
        "primary_scorecard_regression_vs_base_path_control"
        in scorer.proxy_selection_blockers(repair)
    )
