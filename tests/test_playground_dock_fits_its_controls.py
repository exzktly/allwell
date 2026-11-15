"""The Playground's controls dock is as wide as its controls need, and no narrower than the desk's.

The dock beside the preview was pinned to ``Metrics.dock_w`` (360 px) with a horizontal axis that never scrolls,
while phone layout grows its rows to touch size: on the iPhone Duo's screens (1.5 px a phone point) its controls
needed 402 px, and 67 px of every row was cut off — ``content 402px in a 335px viewport`` in the page-render test,
on ``origin/main`` too. ``_StackWhenNarrow`` now gives the dock the larger of the two, and stacks it under the
preview when the page cannot hold that beside a usable preview.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from review.features.image_browser.playground.tab_view import _CANVAS_MIN_W, _StackWhenNarrow
from ui import theme


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _page(controls_min_w: int):
    """The page as ``build_playground_tab`` lays it out: a body holding the preview and the dock, the dock a
    scroll area over the controls."""
    body = QWidget()
    QHBoxLayout(body)
    canvas = QWidget()
    body.layout().addWidget(canvas, 1)
    dock = QWidget()
    outer = QVBoxLayout(dock)
    outer.setContentsMargins(1, 0, 0, 0)
    scroll = QScrollArea(dock)
    scroll.setObjectName("PlaygroundDockScroll")
    scroll.setWidgetResizable(True)
    outer.addWidget(scroll)
    controls = QWidget()
    controls.setMinimumWidth(controls_min_w)
    scroll.setWidget(controls)
    body.layout().addWidget(dock)
    return body, canvas, dock, scroll


def _settle(qapp):
    for _ in range(5):
        qapp.processEvents()


def test_the_dock_takes_what_its_controls_need_when_that_is_more_than_the_desk_s(qapp):
    """Wrong implementation: the fixed ``Metrics.dock_w``, which cut every row short."""
    body, canvas, dock, scroll = _page(402)
    try:
        need = 402 + scroll.verticalScrollBar().sizeHint().width() + 1
        body.resize(need + _CANVAS_MIN_W + 100, 600)
        body.show()
        stack = _StackWhenNarrow(body, canvas, dock)
        stack.apply()
        _settle(qapp)
        assert body.layout().direction() == body.layout().Direction.LeftToRight
        assert dock.width() == need > theme.Metrics.dock_w
        assert scroll.viewport().width() >= 402
    finally:
        body.deleteLater()


def test_a_dock_of_desk_sized_controls_keeps_the_desk_s_width(qapp):
    body, canvas, dock, _scroll = _page(200)
    try:
        body.resize(theme.Metrics.dock_w + _CANVAS_MIN_W + 100, 600)
        body.show()
        _StackWhenNarrow(body, canvas, dock).apply()
        _settle(qapp)
        assert dock.width() == theme.Metrics.dock_w
    finally:
        body.deleteLater()


def test_a_page_too_narrow_for_what_the_dock_needs_stacks_it(qapp):
    """Narrow is judged against the width the dock needs, not the desk's: a page wide enough for 360 px of dock
    beside the preview but not for 402 stacks the dock under it at the page's full width."""
    body, canvas, dock, scroll = _page(402)
    try:
        body.resize(theme.Metrics.dock_w + _CANVAS_MIN_W + 10, 600)
        body.show()
        _StackWhenNarrow(body, canvas, dock).apply()
        _settle(qapp)
        assert body.layout().direction() == body.layout().Direction.TopToBottom
    finally:
        body.deleteLater()
