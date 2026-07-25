"""The optimizer-curve window.

A non-modal ``QDialog`` holding one matplotlib canvas, opened by the Cell Gating
controller after a ThreshFracOn optimization run. It is deliberately independent
of the controller — it re-derives the plotted curve from the same
:mod:`gating_compute` scan the optimizer ran, so it imports downward only and the
controller can call it without a cycle.
"""

from __future__ import annotations

import numpy as np

from PySide6.QtWidgets import QDialog, QVBoxLayout

from ui.theme import get_color
from well_viewer.data.data_loading import get_rows
from well_viewer.features.grouping.cell_gating.gating_compute import (
    ThreshFracOptimizationResult,
    _opt_curve_ylim,
    _timepoint_label,
    _valid_values_for_channel,
    scan_fc_fractions,
)


def _cell_gating_show_opt_curve_popup(
    ctx,
    *,
    channel_key: str,
    well_a: str,
    well_b: str,
    timepoint,
    lo: float,
    hi: float,
    steps: int,
    result: ThreshFracOptimizationResult,
    fc_kwargs: dict | None = None,
) -> None:
    """Open (or refresh) a non-modal window plotting the optimizer's diff
    curve. Re-derives values_a, values_b, and the per-threshold fractions
    locally (through the SAME fold-change scan the optimizer uses) so the
    plotted curve matches the metric ``argmax`` ran on.

    The window stays open across consecutive Optimize clicks — subsequent
    clicks just repaint the existing axes, so the user can iterate on
    wells / timepoints / range and watch the curve update.
    """
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    ratios = getattr(ctx.state.channel, "ratio_index", None) or {}
    values_a = _valid_values_for_channel(
        get_rows(ctx, well_a), channel_key, ratios, timepoint,
    )
    values_b = _valid_values_for_channel(
        get_rows(ctx, well_b), channel_key, ratios, timepoint,
    )
    thresholds = np.linspace(lo, hi, int(steps))
    # Use the SAME scan the optimizer ran (raw, or fold-change-adjusted) so the
    # popup curve is exactly the metric argmax operated on.
    frac_a, frac_b, diffs = scan_fc_fractions(
        thresholds, values_a, values_b, **(fc_kwargs or {}),
    )

    popup = ctx.runtime.cell_gating.opt_curve_popup
    if popup is None or not popup.isVisible():
        popup = QDialog(ctx.runtime.cell_gating.frame or ctx.window)
        popup.setWindowTitle("Optimizer curve")
        popup.resize(820, 460)
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(8, 8, 8, 8)
        fig = Figure(figsize=(7.8, 4.4), dpi=100)
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas)
        # Wire the matplotlib toolbar so the user can pan/zoom/save the
        # curve while keeping the popup non-modal.
        from widgets.mpl_toolbar import MplToolbar
        toolbar = MplToolbar(canvas, popup)
        layout.addWidget(toolbar)
        popup.finished.connect(
            lambda _r: setattr(ctx.runtime.cell_gating, "opt_curve_popup", None)
        )
        popup._fig = fig
        popup._canvas = canvas
        ctx.runtime.cell_gating.opt_curve_popup = popup
        popup.show()

    fig = popup._fig
    fig.clear()
    bg_panel = get_color("BG_PANEL")
    txt_pri = get_color("TXT_PRI")
    txt_mut = get_color("TXT_MUT")
    accent = get_color("ACCENT")
    fig.patch.set_facecolor(bg_panel)
    ax = fig.add_subplot(111)
    ax.set_facecolor(bg_panel)
    for spine in ax.spines.values():
        spine.set_color(txt_mut)
    ax.tick_params(colors=txt_mut)

    ax.plot(thresholds, frac_a, color="#4FC3F7", lw=1.4,
            label=f"fraction-on({well_a})")
    ax.plot(thresholds, frac_b, color="#FF8A65", lw=1.4,
            label=f"fraction-on({well_b})")
    ax.plot(thresholds, diffs, color=accent, lw=1.8,
            label="|Δ| / max(|fa|, |fb|)")
    ax.axvline(
        result.threshold, color=txt_pri, lw=1.2, ls="--",
        label=f"chosen threshold = {result.threshold:.4g}",
    )
    ax.set_xlim(float(thresholds[0]), float(thresholds[-1]))
    ax.set_ylim(*_opt_curve_ylim(frac_a, frac_b, diffs))
    ax.set_xlabel(f"Threshold ({channel_key})", color=txt_pri)
    ax.set_ylabel("Fraction-on / relative gap", color=txt_pri)
    tp_note = "" if timepoint is None else f" @ {_timepoint_label(timepoint)}"
    ax.set_title(
        f"{channel_key}: {well_a} vs {well_b}{tp_note} "
        f"— scan {lo:g}…{hi:g}, {int(steps)} points",
        color=txt_pri, fontsize=11,
    )
    leg = ax.legend(loc="upper right", framealpha=0.0, facecolor="none", fontsize=9)
    if leg is not None:
        for txt in leg.get_texts():
            txt.set_color(txt_pri)
    fig.tight_layout()
    popup._canvas.draw_idle()
    popup.raise_()
    popup.activateWindow()
