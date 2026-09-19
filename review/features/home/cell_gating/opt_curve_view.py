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

from ui import theme
from review.data.data_loading import get_rows
from pipeline.threshold_optimize import (
    OBJECTIVE_SUPPORTED,
    candidate_thresholds,
    counts_above,
    floor_unsupported,
    supported_separation,
)
from review.features.home.cell_gating.gating_compute import (
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
    # Re-derive the SAME scan the optimizer ran, on the same grid and the same
    # objective, so the curve is the metric the search operated on and not a
    # plausible-looking neighbour of it. The two branches diverge exactly where
    # the optimizer's do: with no fold change the score is the confidence-bounded
    # separation, otherwise the floored relative gap.
    if result.objective == OBJECTIVE_SUPPORTED:
        thresholds = candidate_thresholds(
            values_a, values_b, lo=lo, hi=hi, max_candidates=int(steps))
        if thresholds.size == 0:
            return
        k_a = counts_above(np.sort(values_a), thresholds)
        k_b = counts_above(np.sort(values_b), thresholds)
        frac_a = k_a / max(values_a.size, 1)
        frac_b = k_b / max(values_b.size, 1)
        diffs = supported_separation(k_a, values_a.size, k_b, values_b.size,
                                     weight=result.weight)
        score_label = "separation, 95% lower bound"
        y_label = "Fraction-on / supported separation"
    else:
        thresholds = np.linspace(lo, hi, int(steps))
        frac_a, frac_b, diffs = scan_fc_fractions(
            thresholds, values_a, values_b, **(fc_kwargs or {}),
        )
        diffs, _floor = floor_unsupported(diffs, values_a, values_b, thresholds)
        # The mask is -inf, which no axis can scale to. Zero is this score's own
        # "no separation worth having", which is what an unsupported cut has.
        diffs = np.where(np.isfinite(diffs), diffs, 0.0)
        score_label = "|Δ| / max(|fa|, |fb|)"
        y_label = "Fraction-on / relative gap"

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
    bg_panel = theme.Colors.panel
    txt_pri = theme.Colors.text_primary
    txt_mut = theme.Colors.text_muted
    accent = theme.Colors.accent
    fig.patch.set_facecolor(bg_panel)
    ax = fig.add_subplot(111)
    ax.set_facecolor(bg_panel)
    for spine in ax.spines.values():
        spine.set_color(txt_mut)
    ax.tick_params(colors=txt_mut)

    ax.plot(thresholds, frac_a, color=theme.Colors.gate_curve_a, lw=1.4,
            label=f"fraction-on({well_a})")
    ax.plot(thresholds, frac_b, color=theme.Colors.gate_curve_b, lw=1.4,
            label=f"fraction-on({well_b})")
    ax.plot(thresholds, diffs, color=accent, lw=1.8, label=score_label)
    ax.axvline(
        result.threshold, color=txt_pri, lw=1.2, ls="--",
        label=f"chosen threshold = {result.threshold:.4g}",
    )
    ax.set_xlim(float(thresholds[0]), float(thresholds[-1]))
    ax.set_ylim(*_opt_curve_ylim(frac_a, frac_b, diffs))
    ax.set_xlabel(f"Threshold ({channel_key})", color=txt_pri)
    ax.set_ylabel(y_label, color=txt_pri)
    tp_note = "" if timepoint is None else f" @ {_timepoint_label(timepoint)}"
    ax.set_title(
        f"{channel_key}: {well_a} vs {well_b}{tp_note} "
        f"— scan {lo:g}…{hi:g}, {thresholds.size:,} candidate cut(s)",
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
