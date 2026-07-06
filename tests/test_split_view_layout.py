"""Split View: equal panes + a grabbable divider.

Two regressions:
- ``enter_split`` used ``setSizes([1, 1])`` on a still-hidden splitter, so the
  two panes settled at their (unequal) minimum size hints instead of 50/50.
  Equal LARGE targets split evenly regardless of per-pane minimums.
- The ``QSplitter::handle`` QSS rule had no width, collapsing the grab area to
  ~1px (and overriding setHandleWidth); the theme now sizes the handle.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.qt


def test_equal_large_sizes_split_evenly_despite_unequal_minimums(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QSplitter, QWidget

    split = QSplitter(Qt.Horizontal)
    a, b = QWidget(), QWidget()
    a.setMinimumWidth(100)
    b.setMinimumWidth(300)  # the asymmetry that made [1, 1] settle lopsided
    split.addWidget(a)
    split.addWidget(b)
    split.resize(800, 400)

    split.setSizes([1_000_000, 1_000_000])
    sizes = split.sizes()
    assert abs(sizes[0] - sizes[1]) <= 4, f"panes not ~50/50: {sizes}"


def test_theme_qss_sizes_the_splitter_handle():
    import theme
    qss = theme.qss()
    assert "QSplitter::handle:horizontal" in qss
    assert "width: 6px" in qss
    assert "QSplitter::handle:vertical" in qss
