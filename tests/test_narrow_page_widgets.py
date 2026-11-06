"""Widgets and layouts that let a page shrink to a narrow viewport.

A page in a scroll area with the horizontal bar off cannot be reached past its
right edge, so anything that holds the page wider than its viewport hides part
of it. Each test here measures one way that happened and drives the widget, not
a stand-in for it. The page-render harness (``tests/test_every_page_renders.py``)
is the end-to-end proof; these are the cheap, per-widget guard.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton,
    QVBoxLayout, QWidget,
)

pytestmark = pytest.mark.qt

_LONG = ("This dataset was analysed before border contact was recorded, so nothing can be "
         "excluded — Force re-run the analysis to record it, and then the numbers here "
         "will be the run's own rather than an estimate drawn from the folder.")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _settle(qapp) -> None:
    for _ in range(3):
        qapp.processEvents()


# ── WrapRadioRow ────────────────────────────────────────────────────────────

def test_a_wrapped_radio_does_not_pin_the_page_to_its_text_width(qapp):
    from PySide6.QtWidgets import QRadioButton

    from widgets.wrap_radio import WrapRadioRow
    label = "Per-channel intensity minimum (table below)"
    plain = QRadioButton(label)
    wrapped = WrapRadioRow(label)
    # The plain radio's minimum is its whole text; the wrapped one's is about a word.
    assert plain.minimumSizeHint().width() > 250
    assert wrapped.minimumSizeHint().width() < 150
    assert wrapped.text() == label


def test_pressing_the_label_selects_the_radio_and_the_radio_still_groups(qapp):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    from widgets.wrap_radio import WrapRadioRow
    a, b = WrapRadioRow("first choice"), WrapRadioRow("second choice")
    group = QButtonGroup()
    group.setExclusive(True)
    group.addButton(a.radio)
    group.addButton(b.radio)
    a.radio.setChecked(True)
    point = QPointF(2, 2)
    press = QMouseEvent(QEvent.MouseButtonPress, point, point, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    b._lbl.mousePressEvent(press)
    assert b.radio.isChecked() and not a.radio.isChecked()


# ── SqueezeWrapLabel ────────────────────────────────────────────────────────

def test_a_squeeze_label_is_one_line_when_there_is_room_and_wraps_when_there_is_not(qapp):
    from widgets.squeeze_label import SqueezeWrapLabel
    text = "MCHERRY Channel:"
    plain = QLabel(text)
    squeeze = SqueezeWrapLabel(text)
    one_line = plain.sizeHint().width()
    assert abs(squeeze.sizeHint().width() - one_line) <= 2
    assert abs(squeeze.sizeHint().height() - plain.sizeHint().height()) <= 4
    # ...and gives way, wrapping at its words, where the page is narrow.
    assert squeeze.minimumSizeHint().width() < one_line - 20
    assert squeeze.heightForWidth(squeeze.minimumSizeHint().width()) > plain.sizeHint().height()


def test_a_squeeze_label_is_measured_in_the_font_the_stylesheet_gives_it(qapp):
    """A layout asks for the size hint before the label is shown, and the application stylesheet's font only
    reaches a label when it is polished: measured before that, the text is sized in a smaller font than it is
    drawn in. The suite's other tests leave the real stylesheet on the application, which is how this showed."""
    from widgets.squeeze_label import SqueezeWrapLabel
    before = qapp.styleSheet()
    qapp.setStyleSheet("QLabel { font-size: 24px; }")
    try:
        text = "MCHERRY Channel:"
        plain, squeeze = QLabel(text), SqueezeWrapLabel(text)
        assert abs(squeeze.sizeHint().width() - plain.sizeHint().width()) <= 2
        assert squeeze.sizeHint().width() > QLabel(text).fontMetrics().horizontalAdvance(text) + 20
    finally:
        qapp.setStyleSheet(before)


# ── CappedLabel ─────────────────────────────────────────────────────────────

def test_a_capped_label_reserves_the_height_of_the_width_it_is_drawn_at(qapp):
    from widgets.capped_label import CappedLabel
    plain = QLabel(_LONG)
    plain.setWordWrap(True)
    capped = CappedLabel(_LONG)
    capped.setWordWrap(True)
    capped.setMaximumWidth(400)
    # Offered far more than its cap, a plain label answers for the whole offer.
    assert plain.heightForWidth(1400) < plain.heightForWidth(400)
    assert capped.heightForWidth(1400) == plain.heightForWidth(400)


def test_a_container_of_a_capped_label_is_tall_enough_for_its_wrapped_lines(qapp):
    from widgets.capped_label import CappedLabel
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    label = CappedLabel(_LONG, box)
    label.setWordWrap(True)
    label.setMaximumWidth(400)
    lay.addWidget(label)
    assert lay.totalHeightForWidth(1400) == label.heightForWidth(400)


# ── Playground dock ─────────────────────────────────────────────────────────

def _playground_body(width: int, height: int):
    from ui import theme
    body = QWidget()
    lay = QHBoxLayout(body)
    lay.setContentsMargins(12, 0, 12, 12)
    canvas = QWidget(body)
    dock = QWidget(body)
    QVBoxLayout(dock)
    dock.setFixedWidth(theme.Metrics.dock_w)
    lay.addWidget(canvas, 1)
    lay.addWidget(dock)
    from review.features.image_browser.playground.tab_view import _StackWhenNarrow
    stack = _StackWhenNarrow(body, canvas, dock)
    body.resize(width, height)
    body.show()
    return body, canvas, dock, stack


def test_the_playground_dock_goes_below_the_preview_on_a_narrow_page(qapp):
    body, canvas, dock, _stack = _playground_body(432, 800)
    _settle(qapp)
    assert body.layout().direction() == QBoxLayout.TopToBottom
    # The controls take the page's width, not a fixed 360 px strip off its right edge.
    assert dock.width() == 432 - 24
    assert canvas.width() == 432 - 24
    body.close()


def test_the_playground_dock_stays_beside_the_preview_on_a_wide_page(qapp):
    from ui import theme
    body, canvas, dock, _stack = _playground_body(936, 432)
    _settle(qapp)
    assert body.layout().direction() == QBoxLayout.LeftToRight
    assert dock.width() == theme.Metrics.dock_w
    assert canvas.width() > 280
    body.close()


def test_the_playground_dock_follows_the_page_back_and_forth(qapp):
    from ui import theme
    body, _canvas, dock, _stack = _playground_body(936, 432)
    _settle(qapp)
    body.resize(432, 800)
    _settle(qapp)
    assert body.layout().direction() == QBoxLayout.TopToBottom
    body.resize(936, 432)
    _settle(qapp)
    assert body.layout().direction() == QBoxLayout.LeftToRight
    assert dock.width() == theme.Metrics.dock_w
    body.close()


# ── Batch Export's side-by-side decision ────────────────────────────────────

def test_batch_export_stacks_when_the_explicit_left_minimum_does_not_fit(qapp):
    """The group editor sets ``setMinimumWidth(420)``, which overrides its much
    smaller size hint; counting the hint alone said the two halves fit in a
    viewport 114 px too narrow for them, and the page overflowed with no
    horizontal scroll bar."""
    from review.features.batch_export.base_panel import BatchExportPanel
    left = QWidget()
    left.setMinimumWidth(420)
    right = QWidget()
    right.setMinimumWidth(866)
    outer = QBoxLayout(QBoxLayout.LeftToRight)
    sep = QFrame()
    sep.setFixedWidth(1)
    panel = SimpleNamespace(
        _use_sidebar_groups=False, _split_outer=outer, _split_left=left,
        _split_right=right, _split_sep=sep, _split_right_index=2,
        _effective_min_width=BatchExportPanel._effective_min_width,
        _viewport_width=lambda: 1260)
    # 420 + 866 + 1 = 1287 needed; 1260 is offered.
    BatchExportPanel.apply_split_orientation(panel, 1260)
    assert outer.direction() == QBoxLayout.TopToBottom
    # And with room for both (plus the slack) it goes back beside.
    BatchExportPanel.apply_split_orientation(panel, 1287 + 24 + 61)
    assert outer.direction() == QBoxLayout.LeftToRight


# ── Pipeline Info's Parameter column ────────────────────────────────────────

def test_a_long_parameter_name_leaves_the_value_column_in_view(qapp, tmp_path):
    """The Parameter column took its widest visible name's width — the top-level
    "All parameters (raw pipeline_info.json)" row is about 280 px — so on a
    phone-width page the Value column sat past the right edge. The column now
    stops at half the viewport and the Value column keeps the rest."""
    import json

    from tests.test_pipeline_info_view import _stub_app
    from review.features.home.pipeline_info.tab_view import build_pipeline_info_subtab
    (tmp_path / "pipeline_info.json").write_text(json.dumps({
        "qc_options": {"background": "top_hat"}}))
    app = _stub_app(tmp_path)
    parent = QWidget()
    build_pipeline_info_subtab(app, parent)
    tree = app._pipeline_info_tree
    parent.resize(414, 700)
    parent.show()
    _settle(qapp)
    viewport = tree.viewport().width()
    assert viewport < 414
    assert tree.columnWidth(0) <= viewport // 2
    assert tree.columnWidth(1) >= viewport - viewport // 2 - 2
    parent.close()


# ── QC Triage's action row ──────────────────────────────────────────────────

def test_triage_buttons_keep_their_whole_caption_on_a_phone_width_page(qapp, tmp_path):
    from tests.test_qc_triage import _ctx
    from review.features.home.triage.tab_view import build_triage_subtab
    ctx = _ctx(tmp_path, {})
    ctx.widgets = SimpleNamespace(register=lambda _name, widget: widget)
    ctx.runtime = SimpleNamespace(triage=SimpleNamespace(refresh=None))
    ctx.notify = SimpleNamespace(set_status=lambda *_a, **_k: None)
    parent = QWidget()
    build_triage_subtab(ctx, parent)
    # A top-level widget will not be resized below its layout's minimum; the page
    # is a child of a stack that is, so let this one be squeezed as it is there.
    parent.layout().setSizeConstraint(QLayout.SetNoConstraint)
    parent.resize(414, 700)
    parent.show()
    _settle(qapp)
    buttons = parent.findChildren(QPushButton)
    assert len(buttons) == 6
    for btn in buttons:
        assert btn.width() >= btn.minimumSizeHint().width(), (
            f"{btn.text()!r} is {btn.width()} px wide and needs {btn.minimumSizeHint().width()}")
    parent.close()


# ── Playground dock combos ──────────────────────────────────────────────────

_LONG_NAMES = ("A01_DAPI_Nanowell_Plate_Overview_Stitched_Montage_Full_Well_Raw_Channel",
               "A01_GFP_Nanowell_Plate_Overview_Stitched_Montage_Full_Well_Raw_Channel")


def test_a_dock_combo_is_not_as_wide_as_its_longest_item(qapp):
    """A nanowell run's channel names are long, and a plain combo's minimum width
    is its longest item — 650 px for the Playground dock's content, in a 349 px
    viewport that cannot scroll sideways."""
    from PySide6.QtWidgets import QComboBox

    from review.features.image_browser.playground.tab_view import _dock_combo
    plain = QComboBox()
    plain.addItems(_LONG_NAMES)
    host = QWidget()
    dock = _dock_combo(host)
    dock.addItems(_LONG_NAMES)
    assert plain.minimumSizeHint().width() > 400          # the premise: it does pin the page
    assert dock.minimumSizeHint().width() < 200
    assert dock.itemText(0) == _LONG_NAMES[0]             # and the items themselves are whole
