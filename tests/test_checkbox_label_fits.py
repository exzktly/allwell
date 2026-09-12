"""A checkbox on a control row is wide enough for its own label.

Reported on macOS: the Distribution tab's ``log x`` / ``log y`` labels were cut
off, with the neighbouring control butting straight up against the cut, at every
window width. Width-independence is what places the defect — the row had space,
so the clipping is the checkbox's own size, not pressure from the flow.

``QCheckBox.sizeHint()`` budgets an indicator, the ``spacing`` gap and the text.
A style that puts the label further right than that budget assumed leaves the
tail outside the widget; ``FlowLayout`` then packs the next item at
``x + width + spacing``, so the neighbour lands against the cut instead of
leaving a gap that would have made the clipping obvious.

None of this reproduces under Fusion, which is what CI and every Linux dev box
run — the same blind spot ``widgets/flow_layout.py`` documents for
``QMacStyle``'s layout spacing. So the divergence is modelled here by a checkbox
whose ``sizeHint`` under-reports what its style will paint, and the guard is
that the fit helper closes whatever gap the *actual* style leaves.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QCheckBox

from widgets.checkbox_fit import fit_checkbox_width, label_shortfall
from widgets.control_band import ControlBand


class _UnderReportingCheckBox(QCheckBox):
    """A checkbox whose ``sizeHint`` is narrower than its style will paint.

    That *is* the macOS shape: the hint budgets an indicator, the ``spacing``
    gap and the text, and the style then lays the label out needing more than
    the hint costed, so the tail falls outside the widget.

    Modelled on the widget rather than with a ``QProxyStyle`` overriding
    ``SE_CheckBoxContents``. That was the first attempt and it silently did
    nothing under the fast lane: once a global stylesheet is installed Qt wraps
    the widget's style in ``QStyleSheetStyle``, which computes checkbox geometry
    from the stylesheet itself and never delegates the sub-element back to the
    proxy. The case passed alone and was absorbed in the lane, reporting a
    shortfall of -1 — caught only because the premise assertion below refuses to
    let the rest of the file run against a shape it could not reproduce.
    """

    def __init__(self, text: str, deficit: int = 0):
        super().__init__(text)
        self._deficit = deficit

    def set_deficit(self, deficit: int) -> None:
        self._deficit = deficit
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(max(1, hint.width() - self._deficit), hint.height())


def _too_narrow(cb_or_text, overshoot: int = 4) -> QCheckBox:
    """A checkbox whose hint falls *overshoot* px short of holding its label.

    The deficit is measured, not fixed. How much slack a style leaves depends on
    the ambient stylesheet — bare Fusion leaves ~6px for ``log x``, the
    application's own QSS leaves ~1 — so a hard-coded number reproduces the
    defect in one run and not the other. Deriving it from the slack actually
    present makes the case bite wherever it runs.
    """
    text = cb_or_text if isinstance(cb_or_text, str) else cb_or_text.text()
    cb = _UnderReportingCheckBox(text)
    slack = -label_shortfall(cb)
    cb.set_deficit(max(0, slack) + overshoot)
    return cb


def test_a_diverging_style_clips_the_label_before_the_fix(qtbot):
    """The premise: without the floor, the label does not fit."""
    cb = _too_narrow("log x")
    qtbot.addWidget(cb)
    assert label_shortfall(cb) > 0, (
        "The under-reporting checkbox no longer reproduces the reported "
        "shape, so every assertion below is measuring nothing."
    )


def test_the_fit_closes_the_gap(qtbot):
    cb = _too_narrow("log x")
    qtbot.addWidget(cb)
    before = label_shortfall(cb)
    added = fit_checkbox_width(cb)

    assert added == before
    # The assertion that matters: re-measured at the width the widget will now
    # be laid out at, the label fits. Asserting only that minimumWidth grew
    # would pass while the text was still cut — the first version of this test
    # did exactly that, and the band cases below are what caught it.
    assert label_shortfall(cb) <= 0
    assert cb.minimumWidth() > cb.sizeHint().width()


def test_the_fit_is_a_no_op_when_the_label_already_fits(qtbot):
    """Under Fusion the reserve runs wider than the text, so nothing changes."""
    cb = QCheckBox("log x")
    qtbot.addWidget(cb)
    assert label_shortfall(cb) <= 0
    assert fit_checkbox_width(cb) == 0
    assert cb.minimumWidth() == 0


def test_a_textless_checkbox_is_left_alone(qtbot):
    """The WrapCheckBox composite draws its label in a separate widget."""
    cb = _too_narrow("")
    qtbot.addWidget(cb)
    assert label_shortfall(cb) == 0
    assert fit_checkbox_width(cb) == 0


def test_the_band_fits_a_checkbox_added_as_a_scope(qtbot):
    """The rule lives on the band, so no builder has to remember it."""
    band = ControlBand()
    qtbot.addWidget(band)
    cb = _too_narrow("log y")
    band.add_scope("", cb)
    assert label_shortfall(cb) <= 0, (
        "ControlBand.add_scope left a checkbox narrower than its own label."
    )


def test_the_band_fits_a_checkbox_inside_a_labelled_group(qtbot):
    """A checkbox bundled with a caption goes through the same path."""
    band = ControlBand()
    qtbot.addWidget(band)
    cb = _too_narrow("log x")
    band.add_scope("Axes", cb)
    assert label_shortfall(cb) <= 0


def test_the_band_fits_a_checkbox_added_as_an_action(qtbot):
    band = ControlBand()
    qtbot.addWidget(band)
    cb = _too_narrow("log x")
    band.add_action(cb)
    assert label_shortfall(cb) <= 0


def test_the_distribution_log_boxes_go_through_the_band(viewer_app, qtbot):
    """End to end: the two boxes from the report reach the band's fit.

    Full-app because the defect is in how the tab builds them, and a unit test
    of the helper cannot show that this tab reaches it.

    **Characterisation on Linux, not a guard.** Fusion leaves ~6px of slack for
    these labels, so the shortfall is negative here whether or not the fit runs
    — this case passes on ``origin/main`` too. It bites only under a style that
    reserves less than the hint budgeted, which is the reported macOS shape and
    is what the ``_UnderReportingCheckBox`` cases above measure. Kept because it is
    the only case that pins *these two widgets* to the path that fixes them.
    """
    from PySide6.QtWidgets import QWidget
    from review.features.distribution.tab_view import build_distribution_tab

    parent = QWidget()
    qtbot.addWidget(parent)
    build_distribution_tab(viewer_app._ctx, parent)

    for handle in ("_distribution_log_x_cb", "_distribution_log_y_cb"):
        cb = getattr(viewer_app, handle)
        assert label_shortfall(cb) <= 0, (
            f"{handle} is narrower than its own label under the active style."
        )
