"""Image Table per-cell hover pixel-value readout (``_install_cell_events``).

Guards the regression where the intensity readout appeared only while the mouse
was moving and vanished the moment it stopped: each cell label carries a static
combo-title tooltip (``setToolTip``), so when the cursor rested Qt fired a
``QEvent.ToolTip`` and showed *that* instead of the live pixel readout. The
event filter must intercept ``QEvent.ToolTip``, re-show the readout, and consume
the event so the value stays put while the cursor is still.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QToolTip  # noqa: E402

from well_viewer.features.image_browser.image_table import controller as C  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _ctx():
    # _hover_text only reaches ctx.state.dataset.well_labels via well_display_label.
    return SimpleNamespace(
        state=SimpleNamespace(dataset=SimpleNamespace(well_labels={})),
    )


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(QToolTip, "showText", staticmethod(lambda *a, **k: calls.append(a)))
    monkeypatch.setattr(QToolTip, "hideText", staticmethod(lambda *a, **k: None))
    return calls


def _label(size=10, pm=10):
    lbl = QLabel()
    lbl.resize(size, size)
    lbl.setPixmap(QPixmap(pm, pm))
    lbl._raw_arr = np.arange(100, dtype=np.int32).reshape(10, 10)  # (5,5) == 55
    lbl.setToolTip("A1  GFP  T:1")  # the static tooltip that used to win on rest
    C._install_cell_events(_ctx(), lbl, None, "A1", "GFP", "1", "")
    return lbl


class _FakeMove:
    def __init__(self, x, y):
        self._p = QPointF(x, y)

    def type(self):
        return QEvent.MouseMove

    def position(self):
        return self._p

    def globalPosition(self):
        return self._p  # QPointF has .toPoint()


class _FakeToolTip:
    def __init__(self, x, y):
        self._p = QPoint(x, y)

    def type(self):
        return QEvent.ToolTip

    def pos(self):
        return self._p

    def globalPos(self):
        return self._p


def test_move_shows_pixel_readout(monkeypatch):
    calls = _capture(monkeypatch)
    lbl = _label()
    lbl._image_table_event_filter.eventFilter(lbl, _FakeMove(5, 5))
    assert calls, "moving over the image should show the readout"
    assert "(5, 5) = 55" in calls[-1][1] and "A01 GFP T:1" in calls[-1][1]


def test_tooltip_event_reshows_readout_and_is_consumed(monkeypatch):
    """The fix: on ToolTip (cursor at rest) re-show the readout and swallow the
    event, so the static combo tooltip never replaces the live value."""
    calls = _capture(monkeypatch)
    lbl = _label()
    handled = lbl._image_table_event_filter.eventFilter(lbl, _FakeToolTip(5, 5))
    assert handled is True, "ToolTip over the image must be consumed"
    assert calls and "(5, 5) = 55" in calls[-1][1]


def test_tooltip_off_image_falls_through_to_static(monkeypatch):
    """Over the label padding (no image pixel) the ToolTip is NOT consumed, so
    Qt's static combo-title tooltip still shows there."""
    _capture(monkeypatch)
    lbl = _label(size=20, pm=10)  # 10px pixmap centred in a 20px label → 5px pad
    handled = lbl._image_table_event_filter.eventFilter(lbl, _FakeToolTip(1, 1))
    assert handled is False
