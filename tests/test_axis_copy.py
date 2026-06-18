"""Per-axis "copy this panel" support.

Covers the two load-bearing facts behind the PlotCard hover-copy overlay:
1. ``copy_figure_to_clipboard`` relocated to ``widgets.fig_clipboard`` is still
   re-exported from ``well_viewer.features.export.style`` (the existing call
   sites import it from there), and is the *same* object.
2. ``axis_tight_bbox_inches`` crops to a single subplot — its bbox is strictly
   smaller than the whole figure's tight bbox, with positive extent.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def test_copy_figure_reexport_is_same_object():
    import widgets.fig_clipboard as fc
    from well_viewer.features.export.style import copy_figure_to_clipboard

    assert copy_figure_to_clipboard is fc.copy_figure_to_clipboard


def test_axis_tight_bbox_is_smaller_than_figure():
    import matplotlib
    matplotlib.use("Agg", force=False)
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from widgets.fig_clipboard import axis_tight_bbox_inches

    fig = Figure(figsize=(6, 8))
    FigureCanvasAgg(fig)
    ax0 = fig.add_subplot(2, 1, 1)
    ax1 = fig.add_subplot(2, 1, 2)
    ax0.plot([0, 1, 2], [0, 1, 0])
    ax0.set_title("panel 0")
    ax1.plot([0, 1, 2], [1, 0, 1])
    fig.canvas.draw()

    bb = axis_tight_bbox_inches(ax0)
    assert bb.width > 0 and bb.height > 0

    renderer = fig.canvas.get_renderer()
    # NB: Figure.get_tightbbox already returns *inches* (unlike
    # Axes.get_tightbbox, which returns display pixels) — no transform here.
    full = fig.get_tightbbox(renderer)
    # One of two stacked panels must occupy strictly less area than the whole.
    assert (bb.width * bb.height) < (full.width * full.height)


def test_copy_figure_to_clipboard_status_on_fallback(monkeypatch):
    import matplotlib
    matplotlib.use("Agg", force=False)
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from PySide6.QtWidgets import QApplication

    import widgets.fig_clipboard as fc

    QApplication.instance() or QApplication([])
    # Force the cross-platform Qt clipboard path (skip the macOS pasteboard).
    monkeypatch.setattr(fc, "_macos_pasteboard_write", lambda *a, **k: False)

    fig = Figure(figsize=(3, 2))
    FigureCanvasAgg(fig)
    fig.add_subplot(1, 1, 1).plot([0, 1], [0, 1])

    status = fc.copy_figure_to_clipboard(fig)
    assert isinstance(status, str)
    assert not status.startswith("Copy failed")
    assert QApplication.clipboard().mimeData() is not None
