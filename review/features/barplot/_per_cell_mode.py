"""Bar Plots' per-cell display mode: Bars, Beeswarm, Violin or Histogram, and the Display popover controls that show it.

``BarPlotState`` keeps the mode as three exclusive flags (``swarm``, ``violin``, ``histogram``; all off = Bars), the
shape saved views have always had. :func:`per_cell_mode` is the one reading of them: every site that asks "is a
per-cell mode on, and which" — the renderer's dispatch, its declines, the cell-scope note — asks it, so a fourth mode
cannot be forgotten by one of them. :func:`set_per_cell_mode` is the one writer, and :func:`sync_controls` puts the
popover's **Show** selector, **Smooth** slider and **Bins** box back in step with the state; ``redraw_bars`` calls it
through ``refresh_bar_layout_btn_state`` so a restored view, a reset or a toggle all leave the controls telling the
truth.

Qt-free: it reaches widgets only through ``ctx.widgets``.
"""

from __future__ import annotations

from review.domain.column_histogram import clamp_bins

BARS, BEESWARM, VIOLIN, HISTOGRAM = "bars", "beeswarm", "violin", "histogram"
#: The Show selector's choices, in its order, with their labels.
MODES = ((BARS, "Bars"), (BEESWARM, "Beeswarm"), (VIOLIN, "Violin"), (HISTOGRAM, "Histogram"))
MODE_KEYS = tuple(key for key, _label in MODES)
#: How each per-cell mode is named where a figure or a note points at the control.
MODE_NAMES = {BEESWARM: "Beeswarm", VIOLIN: "Violin", HISTOGRAM: "Histogram"}


def per_cell_mode(bar) -> str:
    """The per-cell mode *bar* (a ``BarPlotState``) is in: ``"histogram"``, ``"violin"``, ``"beeswarm"`` or ``""``
    for bars. A damaged state with two flags on reads in that order of precedence."""
    if getattr(bar, "histogram", False):
        return HISTOGRAM
    if getattr(bar, "violin", False):
        return VIOLIN
    if getattr(bar, "swarm", False):
        return BEESWARM
    return ""


def set_flags(bar, mode: str) -> None:
    """Put *bar* in *mode* (one of :data:`MODE_KEYS`), every other per-cell flag off."""
    bar.swarm = mode == BEESWARM
    bar.violin = mode == VIOLIN
    bar.histogram = mode == HISTOGRAM


def set_per_cell_mode(ctx, mode: str, redraw=None) -> None:
    """The Show selector's handler: *mode* on, the others off, controls synced, then *redraw(ctx)* — the tab's
    ``redraw_bars``, handed in because the renderer imports this module."""
    if mode not in MODE_KEYS:
        return
    set_flags(ctx.state.bar, mode)
    sync_controls(ctx)
    if redraw is not None:
        redraw(ctx)


def set_hist_bins(ctx, bins, redraw=None) -> None:
    """The Bins box's handler: store the count, and *redraw(ctx)* when a histogram is drawn (otherwise nothing it
    draws changes)."""
    bins = clamp_bins(bins)
    if ctx.state.bar.hist_bins == bins:
        return
    ctx.state.bar.hist_bins = bins
    if redraw is not None and per_cell_mode(ctx.state.bar) == HISTOGRAM:
        redraw(ctx)


def sync_controls(ctx) -> None:
    """The Show selector on the state's mode; Smooth usable only under Violin, Bins only under Histogram."""
    mode = per_cell_mode(ctx.state.bar) or BARS
    seg = ctx.widgets.get_or_none("_bar_show_seg")
    slider = ctx.widgets.get_or_none("_violin_slider")
    spin = ctx.widgets.get_or_none("_hist_bins_spin")
    if seg is not None and seg.currentData() != mode:
        blocked = seg.blockSignals(True)
        seg.setCurrentByData(mode)
        seg.blockSignals(blocked)
    if slider is not None:
        slider.setEnabled(mode == VIOLIN)
    if spin is not None:
        spin.setEnabled(mode == HISTOGRAM)
        if spin.value() != ctx.state.bar.hist_bins:
            blocked = spin.blockSignals(True)
            spin.setValue(ctx.state.bar.hist_bins)
            spin.blockSignals(blocked)
