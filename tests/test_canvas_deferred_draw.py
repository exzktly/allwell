"""A canvas's queued draw does nothing once the canvas is deleted.

``FigureCanvasQT.draw_idle`` queues its draw with a receiver-less
``QTimer.singleShot``, so the call arrives even after the canvas is gone and
used to raise ``RuntimeError: Internal C++ object already deleted`` from inside
Qt's event loop. Heat Map draws its empty state while it is built, and it is the
Plotting sub-tab built with the window, so a window closed in the same turn hit
it — and pytest, keeping the unraisable exception, kept the whole window alive
(``tests/test_a_closed_review_app_is_freed.py``). ``widgets.canvas.make_canvas``
guards the queued half; these hold it.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QWidget


def _flush(qapp) -> None:
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qapp.processEvents()


def test_a_draw_queued_before_the_canvas_is_deleted_raises_nothing(qapp, monkeypatch):
    from matplotlib.figure import Figure

    from widgets.canvas import make_canvas

    raised = []
    monkeypatch.setattr(sys, "unraisablehook", raised.append)
    host = QWidget()
    canvas = make_canvas(Figure(), host)
    canvas.draw_idle()
    del canvas
    host.deleteLater()
    del host
    _flush(qapp)
    assert [type(u.exc_value).__name__ for u in raised] == []


def test_a_queued_draw_on_a_live_canvas_still_draws(qapp):
    from matplotlib.figure import Figure

    from widgets.canvas import make_canvas

    host = QWidget()
    host.resize(200, 150)
    canvas = make_canvas(Figure(), host)
    canvas.resize(200, 150)
    drawn = []
    canvas.mpl_connect("draw_event", lambda _evt: drawn.append(True))
    canvas.draw_idle()
    _flush(qapp)
    assert drawn == [True]
    host.deleteLater()
