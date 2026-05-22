"""adjustments_to_prose: pure-function input → output."""
from __future__ import annotations

from figcopy_runner.adjustments_to_prose import to_prose


def test_single_adjustment_no_message():
    assert to_prose({"font.size": 15}, None) == "Adjust: font.size = 15."


def test_single_adjustment_empty_message():
    assert to_prose({"font.size": 15}, "") == "Adjust: font.size = 15."


def test_adjustment_plus_message():
    out = to_prose({"font.size": 15}, "blue please")
    assert out == "Adjust: font.size = 15. blue please"


def test_no_adjustments_passes_message():
    assert to_prose({}, "just blue") == "just blue"
    assert to_prose(None, "just blue") == "just blue"


def test_no_adjustments_no_message():
    assert to_prose({}, None) == ""
    assert to_prose(None, None) == ""
    assert to_prose({}, "") == ""


def test_message_trimmed():
    out = to_prose(None, "   spaced   ")
    assert out == "spaced"


def test_multiple_adjustments_sorted():
    out = to_prose({"font.size": 15, "axes.labelsize": 14}, None)
    # Keys sorted alphabetically for determinism.
    assert out == "Adjust: axes.labelsize = 14, font.size = 15."


def test_integer_renders_without_dot_zero():
    out = to_prose({"font.size": 15.0}, None)
    # 15.0 should render as "15" (no trailing .0 from float).
    assert "15.0" not in out
    assert out == "Adjust: font.size = 15."


def test_float_value_preserved():
    out = to_prose({"lines.linewidth": 1.8}, None)
    assert out == "Adjust: lines.linewidth = 1.8."


def test_string_value_quoted():
    out = to_prose({"axes.prop_cycle": "tableau-10"}, None)
    assert '"tableau-10"' in out


def test_bool_value_lowercased():
    out = to_prose({"legend.frameon": False}, None)
    assert "false" in out
    out2 = to_prose({"legend.frameon": True}, None)
    assert "true" in out2


def test_determinism():
    a = to_prose({"a": 1, "b": 2, "c": 3}, "msg")
    b = to_prose({"c": 3, "b": 2, "a": 1}, "msg")
    assert a == b
