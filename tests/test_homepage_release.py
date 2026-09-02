from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def test_homepage_links_paper_and_embeds_release_assets():
    expected_captions = {
        "README.md": ("Main results.", "Qualitative comparison."),
        "README.zh-CN.md": ("主实验结果。", "定性对比。"),
    }
    for name, captions in expected_captions.items():
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "https://arxiv.org/abs/2608.28814" in text
        assert "docs/assets/evaluation/main-results.png" in text
        assert "docs/assets/evaluation/qualitative-comparison.png" in text
        assert "72.7" in text
        assert "76.4" in text
        assert all(caption in text for caption in captions)
        assert text.index('<h2 id="citation">') > text.index('<h2 id="roadmap">')


def test_main_results_png_is_high_resolution_paper_crop():
    png = ROOT / "docs" / "assets" / "evaluation" / "main-results.png"
    with Image.open(png) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.width >= 2_000
        assert image.height >= 700
