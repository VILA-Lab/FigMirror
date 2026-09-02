from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def test_homepage_links_paper_and_embeds_release_assets():
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "https://arxiv.org/abs/2608.28814" in text
        assert "docs/assets/evaluation/main-results.svg" in text
        assert "docs/assets/evaluation/qualitative-comparison.png" in text
        assert "72.7" in text
        assert "76.4" in text


def test_main_results_svg_matches_paper_table():
    svg = ROOT / "docs" / "assets" / "evaluation" / "main-results.svg"
    root = ET.parse(svg).getroot()

    expected_rows = {
        267: ("FigMirror", "64.4", "77.2", "72.7", "84.0", "72.3", "76.4"),
        339: ("ChartIR", "56.6", "63.9", "61.3", "83.6", "63.1", "70.3"),
        411: ("ChartGalaxy", "58.6", "58.4", "58.5", "80.7", "58.1", "66.0"),
        483: ("Plot2Code", "53.4", "56.8", "55.6", "64.9", "43.0", "50.7"),
        555: ("METAL", "53.7", "50.5", "51.6", "66.2", "43.5", "51.5"),
    }
    for y, expected in expected_rows.items():
        cells = [
            (int(node.attrib["x"]), "".join(node.itertext()).strip())
            for node in root.iter()
            if node.tag.endswith("text") and int(node.attrib.get("y", -1)) == y
        ]
        assert tuple(text for _x, text in sorted(cells)) == expected
