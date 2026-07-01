"""The well-plate tooltip handler must not crash when its window is inactive.

On Windows a ToolTip event can be delivered to the plate while a modal dialog
(the Open-directory picker) owns the event loop; driving QToolTip from an
inactive window then crashes with a native access violation. ``_PlateGrid.event``
now defers to the base handler unless the plate's window is the active one.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QApplication

from widgets.well_plate_selector import WellPlateSelector


def _tooltip_event():
    return QHelpEvent(QEvent.Type.ToolTip, QPoint(5, 5), QPoint(5, 5))


def test_tooltip_ignored_when_window_not_active(qtbot):
    w = WellPlateSelector()
    qtbot.addWidget(w)
    # Not shown / not the active window → the guard path. Must not raise.
    result = w._grid.event(_tooltip_event())
    assert isinstance(result, bool)


def test_tooltip_handled_when_window_active(qtbot, monkeypatch):
    w = WellPlateSelector()
    qtbot.addWidget(w)
    w.show()
    # Force the plate's window to look like the active window so the tooltip
    # branch runs end-to-end without crashing (and consumes the event).
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: w.window()))
    assert w._grid.event(_tooltip_event()) is True
