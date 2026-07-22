"""Drawer supports a right edge (default) and a bottom edge.

The Application Log drawer uses ``edge="bottom"`` so it rises as a wide,
horizontal panel instead of a narrow vertical side panel.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from widgets.drawer import Drawer


def _host(qtbot, w=800, h=600):
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(w, h)
    return host


def test_right_edge_geometry_unchanged(qtbot):
    host = _host(qtbot)
    d = Drawer(host, width_hint=460)
    op, cl = d._open_rect(), d._closed_rect()
    # Full-height vertical panel pinned to the right; closed sits just off-right.
    assert op.width() == 460 and op.height() == 600
    assert op.x() == 800 - 460 and op.y() == 0
    assert cl.x() == 800 and cl.width() == 460


def test_bottom_edge_is_a_wide_horizontal_panel(qtbot):
    host = _host(qtbot)
    d = Drawer(host, edge="bottom", height_fraction=0.4)
    op, cl = d._open_rect(), d._closed_rect()
    # Full width, ~40% height, pinned to the bottom; closed sits just below.
    assert op.width() == 800                      # spans the host width
    assert op.height() == round(600 * 0.4)        # 240
    assert op.x() == 0 and op.y() == 600 - op.height()
    assert cl.y() == 600 and cl.width() == 800    # slid off the bottom


def test_bottom_edge_height_hint_caps_to_host(qtbot):
    host = _host(qtbot, h=300)
    d = Drawer(host, edge="bottom", height_hint=10_000)
    assert d._open_rect().height() == 300         # never taller than the host


def test_unknown_edge_falls_back_to_right(qtbot):
    host = _host(qtbot)
    d = Drawer(host, edge="diagonal", width_hint=200)
    assert d._edge == "right"
    assert d._open_rect().height() == 600
