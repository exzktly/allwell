"""Unit tests for ``WidgetRegistry`` — the name-keyed widget-handle store."""

from __future__ import annotations

import pytest

from review.core.widget_registry import WidgetRegistry


def test_register_and_get():
    reg = WidgetRegistry()
    reg.register("_line_fig", "FIG")
    assert reg.get("_line_fig") == "FIG"


def test_register_returns_the_widget():
    reg = WidgetRegistry()
    # Lets a builder write ``ax = reg.register("_ax", make_ax())``.
    assert reg.register("_ax", "AX") == "AX"


def test_get_missing_raises_key_error():
    with pytest.raises(KeyError):
        WidgetRegistry().get("_absent")


def test_get_or_none():
    reg = WidgetRegistry()
    reg.register("_canvas", "C")
    assert reg.get_or_none("_canvas") == "C"
    assert reg.get_or_none("_absent") is None


def test_has_and_contains():
    reg = WidgetRegistry()
    reg.register("_card", "CARD")
    assert reg.has("_card") is True
    assert "_card" in reg
    assert "_absent" not in reg


def test_len_and_iter():
    reg = WidgetRegistry()
    reg.register("_a", 1)
    reg.register("_b", 2)
    assert len(reg) == 2
    assert sorted(reg) == ["_a", "_b"]
    assert sorted(reg.names()) == ["_a", "_b"]


def test_register_overwrites():
    reg = WidgetRegistry()
    reg.register("_fig", "OLD")
    reg.register("_fig", "NEW")
    assert reg.get("_fig") == "NEW"


def test_clear():
    reg = WidgetRegistry()
    reg.register("_fig", "FIG")
    reg.clear()
    assert len(reg) == 0
    assert "_fig" not in reg
