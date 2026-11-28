"""A phone's plot is sized again when its figure's rows of axes change after it was shown (``PhoneShell._follow_rows``).

Spatial lays its wells out again for a phone held upright — four stacked where two sat abreast — after the phone sized
the canvas for two rows, and each map was drawn a few pixels tall (every-page tour, tracker row S31).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")


def test_a_draw_that_finds_more_rows_sizes_the_canvas_again(qtbot, monkeypatch):
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure

    from core import phone_layout
    from ui.phone_shell import PhoneShell

    monkeypatch.setattr(phone_layout, "is_on", lambda: True)
    canvas = FigureCanvasQTAgg(Figure(figsize=(4.0, 6.0)))
    qtbot.addWidget(canvas)
    sized = []
    shell = SimpleNamespace(size_canvas=lambda c, on: (sized.append(on), c.setProperty("_awRows", 2)))
    for i in range(2):
        canvas.figure.add_subplot(2, 1, i + 1)
    canvas.setProperty("_awRows", 2)
    PhoneShell._follow_rows(shell, canvas)
    canvas.draw()
    assert sized == []                               # the rows it was sized for
    canvas.figure.clear()
    for i in range(4):
        canvas.figure.add_subplot(4, 1, i + 1)
    canvas.draw()
    assert sized == []                               # never inside the draw
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    assert sized == [True]
