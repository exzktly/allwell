"""Per-axis "copy this panel" support.

Covers the two load-bearing facts behind the PlotCard hover-copy overlay:
1. ``copy_figure_to_clipboard`` relocated to ``widgets.fig_clipboard`` is still
   re-exported from ``review.features.export.style`` (the existing call
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
    from review.features.export.style import copy_figure_to_clipboard

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


def test_hidden_sibling_axes_hides_and_restores():
    """Inside the block only *keep_ax* is visible; on exit every sibling's
    original visibility is restored (including a sibling that started hidden)."""
    import matplotlib
    matplotlib.use("Agg", force=False)
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from widgets.fig_clipboard import hidden_sibling_axes

    fig = Figure(figsize=(4, 6))
    FigureCanvasAgg(fig)
    ax0 = fig.add_subplot(3, 1, 1)
    ax1 = fig.add_subplot(3, 1, 2)
    ax2 = fig.add_subplot(3, 1, 3)
    ax2.set_visible(False)                    # a sibling that starts hidden

    with hidden_sibling_axes(fig, ax1):
        assert ax1.get_visible() is True      # target stays visible
        assert ax0.get_visible() is False     # siblings hidden inside the block
        assert ax2.get_visible() is False

    # Original visibility restored on exit.
    assert ax0.get_visible() is True
    assert ax1.get_visible() is True
    assert ax2.get_visible() is False


def test_panel_save_hides_siblings_during_savefig(qapp):
    """The per-panel Saved-Plots crop must render with sibling panels hidden, so a
    neighbour's title / ticks that fall inside the crop rectangle can't bleed in.
    Spy on ``savefig`` to record axis visibility at capture time."""
    from widgets.plot_card import PlotCard

    card = PlotCard(figsize=(3, 4))
    ax0 = card.figure.add_subplot(2, 1, 1)
    ax0.plot([0, 1], [0, 1])
    ax0.set_title("neighbour title")
    ax1 = card.figure.add_subplot(2, 1, 2)
    ax1.plot([0, 1], [1, 0])
    card.set_save_handler(lambda png, label: "ok")

    seen = {}
    real_savefig = card.figure.savefig

    def spy_savefig(*args, **kwargs):
        seen["vis"] = [a.get_visible() for a in card.figure.axes]
        return real_savefig(*args, **kwargs)

    card.figure.savefig = spy_savefig
    card._hover_ax = ax1          # save the BOTTOM panel
    card._on_save_axis_clicked()

    # At savefig time: target visible, neighbour hidden.
    assert seen["vis"] == [False, True]
    # Visibility restored afterwards.
    assert ax0.get_visible() is True
    assert ax1.get_visible() is True


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


def test_a_one_axes_card_offers_the_copy_button(qapp):
    """A single-axes figure carries the hover Copy glyph.

    It did not, on the reasoning that a per-panel copy duplicates "the
    whole-canvas Copy button". That button is gone — the card's header was
    retired when the shared ControlBand took its controls — so the suppression
    removed the only glyph rather than de-duplicating it, and the five
    single-axes plot tabs (Response Curves, Heat Map, Distribution and the two
    Scatter leaves) hovered to a lone save icon.

    The wrong implementation this rules out is the one that shipped: a
    ``_hover_buttons`` that gates Copy on ``len(figure.axes) > 1``.
    """
    from widgets.plot_card import PlotCard

    single = PlotCard(figsize=(3, 2))
    single.figure.add_subplot(1, 1, 1).plot([0, 1], [0, 1])
    assert single._copy_btn in single._hover_buttons()
    # …and it says what it copies. On one axes that is the plot, and calling it
    # "this panel" names a division the reader cannot see.
    assert single._copy_btn.toolTip() == "Copy this plot to the clipboard"

    multi = PlotCard(figsize=(3, 4))
    multi.figure.add_subplot(2, 1, 1).plot([0, 1], [0, 1])
    multi.figure.add_subplot(2, 1, 2).plot([0, 1], [1, 0])
    assert multi._copy_btn in multi._hover_buttons()
    assert multi._copy_btn.toolTip() == "Copy this panel to the clipboard"


def test_a_one_axes_capture_keeps_the_figure_s_own_title(qapp):
    """A per-panel capture of a one-axes figure is the whole figure.

    The two answers differ only in what they discard, and it is worth measuring
    which: an axes-tight crop carries a free ``ax.text`` drawn below the axes
    (Response Curves' omission caption survives), and discards anything the
    *figure* owns — a ``suptitle`` or a ``fig.text`` footnote. Distribution
    titles its single-axes figure with a suptitle, so cropping to the axes
    handed the user an untitled plot that looked like a complete one.

    Asserted as the bbox the capture paths pass to ``savefig`` — ``"tight"``,
    the whole figure — rather than as "the crop is big enough", which a crop
    one point too small would also satisfy.
    """
    from widgets.fig_clipboard import axis_tight_bbox_inches
    from widgets.plot_card import PlotCard

    card = PlotCard(figsize=(6, 4), constrained=False)
    ax = card.figure.add_subplot(1, 1, 1)
    ax.plot([1, 2, 3], [1, 2, 3])
    card.figure.subplots_adjust(bottom=0.3, top=0.8)
    before = axis_tight_bbox_inches(ax, pad_in=0.0)
    card.figure.suptitle("Response vs Dose")
    after = axis_tight_bbox_inches(ax, pad_in=0.0)
    # The premise, measured: the axes crop does not grow to hold the suptitle.
    assert after.y1 == before.y1

    assert card._panel_bbox(ax) == "tight"

    multi = PlotCard(figsize=(3, 4))
    top = multi.figure.add_subplot(2, 1, 1)
    top.plot([0, 1], [0, 1])
    multi.figure.add_subplot(2, 1, 2).plot([0, 1], [1, 0])
    # With siblings the crop is the point of the affordance and stays a bbox.
    assert multi._panel_bbox(top) != "tight"
