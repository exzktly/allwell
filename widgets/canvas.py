"""The one place a matplotlib Qt canvas is built.

A plot canvas has a different *form* in another layout — its DPI, its height per
row of axes, how a swipe on it behaves — so every canvas declares itself to
``widgets.form_registry`` where it is built, and whoever applies a layout's form
finds it by that declaration rather than by recognising its type. Building a
canvas anywhere else is refused by ``tests/test_phone_mode_stays_in_views.py``.

The matplotlib Qt backend is imported inside :func:`make_canvas`, not at module
level: loading it was a measurable part of application start-up, and a tab
builds its canvas only when it is first shown.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from widgets import form_registry


def make_canvas(figure, parent: QWidget | None = None):
    """A ``FigureCanvasQTAgg`` for *figure*, parented to *parent* when given,
    declared as kind ``"canvas"``."""
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

    canvas = FigureCanvasQTAgg(figure)
    if parent is not None:
        canvas.setParent(parent)
    form_registry.declare(canvas, "canvas")
    return canvas
