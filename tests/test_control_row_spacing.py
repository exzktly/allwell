"""Control-row and checkbox geometry must not come from the platform style.

Three macOS-only layout defects traced back to the same habit: asking the Qt
style for a metric, and getting a different answer than Fusion gives — which is
the only style CI ever sees. Nothing here could go red before, because Fusion
answers all of these questions differently *and correctly*.

* ``FlowLayout`` defaulted its spacing to ``-1``, "derive from the style". That
  reads ``pixelMetric(PM_Layout*Spacing)``, which Qt documents as returning -1 on
  styles implementing the size-class-aware ``layoutSpacing()`` instead —
  QMacStyle is one. ``_do_layout`` clamps with ``max(0, …)``, so every row using
  the default packed its controls edge to edge on macOS.
* The checkbox indicator carried a ``margin-right`` on top of ``spacing``, so the
  two stacked into a gap neither of them declares.
* Two widget-level sheets overrode part of the app sheet's indicator rule and
  silently inherited the rest.

These assert the *numbers*, not the appearance, so they hold on any style.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton, QWidget

import theme
from widgets.flow_layout import (
    _DEFAULT_H_SPACING,
    _DEFAULT_V_SPACING,
    FlowLayout,
)
from widgets.toggle_switch import ToggleSwitch
from widgets.wrap_checkbox import WrapCheckBox


def _indicator_geometry(cb: QCheckBox) -> tuple[int, int]:
    """``(indicator slot width, gap to the label)`` as the style will lay it out."""
    cb.ensurePolished()
    opt = QStyleOptionButton()
    opt.initFrom(cb)
    opt.text = cb.text()
    ind = cb.style().subElementRect(QStyle.SE_CheckBoxIndicator, opt, cb)
    contents = cb.style().subElementRect(QStyle.SE_CheckBoxContents, opt, cb)
    return ind.width(), contents.x() - (ind.x() + ind.width())


def test_flow_layout_spacing_does_not_depend_on_the_style(qtbot):
    """The default must be a real number, not whatever the style volunteers.

    A style that answers ``layoutSpacing()`` rather than ``pixelMetric`` returns
    -1 here, which the layout clamps to zero — no gap between any two controls.
    """
    host = QWidget()
    qtbot.addWidget(host)
    lay = FlowLayout(host)
    assert lay.horizontalSpacing() == _DEFAULT_H_SPACING
    assert lay.verticalSpacing() == _DEFAULT_V_SPACING
    assert lay.horizontalSpacing() > 0 and lay.verticalSpacing() > 0


def test_flow_layout_style_derived_spacing_never_collapses(qtbot):
    """``-1`` still means "ask the style" — but never resolves to nothing."""
    host = QWidget()
    qtbot.addWidget(host)
    lay = FlowLayout(host, h_spacing=-1, v_spacing=-1)
    assert lay.horizontalSpacing() > 0
    assert lay.verticalSpacing() > 0


def test_checkbox_gap_is_spacing_alone(qtbot):
    """The indicator↔label gap is ``spacing``, with nothing stacked on top.

    A margin on the indicator adds to ``spacing`` rather than replacing it, so
    the rendered gap silently became the sum of the two.
    """
    host = QWidget()
    qtbot.addWidget(host)
    host.setStyleSheet(theme.qss())
    cb = QCheckBox("Rep-set avg", host)
    slot, gap = _indicator_geometry(cb)
    # 16px content + 1px border each side, and no margin beyond it.
    assert slot == 18, f"indicator slot {slot}px — expected the painted box, 18px"
    assert gap == 8, f"gap {gap}px — expected `spacing` (8px) and nothing else"


def test_wrap_checkbox_indicator_owns_no_extra_space(qtbot):
    """The composite's box sits flush; its layout gap is the only separation.

    ``WrapCheckBox`` pairs a *text-less* checkbox with its own wrapped label, so
    any space the indicator reserves for a label it does not have reads as the
    box floating away from its text.
    """
    host = QWidget()
    qtbot.addWidget(host)
    host.setStyleSheet(theme.qss())
    wcb = WrapCheckBox("Remove debris (drop regions outside the bounds)", host)
    slot, gap = _indicator_geometry(wcb._cb)
    assert slot == 18, f"indicator slot {slot}px — inherited a margin it overrode only partly"
    assert gap == 0, f"gap {gap}px — the text-less box must not reserve a label gap"


def test_toggle_switch_reserves_no_indicator(qtbot):
    """A switch draws itself; the inherited checkbox indicator must vanish.

    Declaring only ``width``/``height`` left the app sheet's border and margin
    in place, so a zero-size indicator still occupied a slot.
    """
    host = QWidget()
    qtbot.addWidget(host)
    host.setStyleSheet(theme.qss())
    sw = ToggleSwitch(parent=host)
    slot, _gap = _indicator_geometry(sw)
    assert slot == 0, f"indicator slot {slot}px — expected none at all"
