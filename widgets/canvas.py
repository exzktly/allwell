"""The one place a matplotlib Qt canvas is built.

A plot canvas has a different *form* in another layout — its DPI, its height per
row of axes, how a swipe on it behaves — so every canvas declares itself to
``widgets.form_registry`` where it is built, and whoever applies a layout's form
finds it by that declaration rather than by recognising its type. Building a
canvas anywhere else is refused by ``tests/test_phone_mode_stays_in_views.py``.

The matplotlib Qt backend is imported inside :func:`make_canvas`, not at module
level: loading it was a measurable part of application start-up, and a tab
builds its canvas only when it is first shown.

**A deferred draw does nothing once the canvas is gone.** ``draw_idle`` queues
the real draw with ``QTimer.singleShot(0, self._draw_idle)``, a call with no
receiver object, so Qt delivers it even when the canvas has been deleted in the
meantime — a window closed in the same turn as a tab that drew into its canvas
(Heat Map draws its empty state while it is built, and it is the Plotting
sub-tab built with the window). Unchecked, the queued call raises ``RuntimeError:
Internal C++ object already deleted`` from inside Qt's event loop, and an
interpreter that keeps that exception (pytest does) keeps its frames, its
canvas and through it the whole window alive. :func:`make_canvas` gives each
canvas a deferred draw that first checks the canvas still exists, and holds it
weakly so the queued call keeps nothing alive either.
"""

from __future__ import annotations

import functools
import weakref

import shiboken6
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
    # ``draw_idle`` queues ``self._draw_idle`` by attribute, so an instance
    # attribute is what it queues (module docstring).
    canvas._draw_idle = functools.partial(_draw_if_alive, weakref.ref(canvas))
    return canvas


def _draw_if_alive(canvas_ref) -> None:
    """The queued half of ``draw_idle``, skipped once the canvas is deleted."""
    canvas = canvas_ref()
    if canvas is None or not shiboken6.isValid(canvas):
        return
    type(canvas)._draw_idle(canvas)
