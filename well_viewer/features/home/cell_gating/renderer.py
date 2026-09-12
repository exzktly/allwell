"""Cell Gating figures: the per-channel cumulative-distribution panel.

Pure matplotlib. Draws into the figure held on ``ctx.runtime.cell_gating`` and
touches no widget beyond the canvas it repaints, so a CDF can be rendered and
snapshot-tested without building the tab. The optimizer's own diff curve is a
separate window and lives in :mod:`opt_curve_view`.
"""

from __future__ import annotations

import numpy as np

from ui import theme
from core.safe import swallowed


def _focus_cdf_xaxis(ax, vals) -> None:
    """Clamp the x-axis to the data range (+ small pad) so a gate line at 0 —
    or far outside the data — doesn't stretch the axis and cram the CDF against
    one edge. Called after the gate axvline so it has the final say."""
    if len(vals) == 0:
        return
    lo = float(np.min(vals))
    hi = float(np.max(vals))
    pad = (hi - lo) * 0.04 if hi > lo else (abs(hi) * 0.05 or 1.0)
    ax.set_xlim(lo - pad, hi + pad)


def _effective_thresh_frac_on(ctx, channel: str) -> float | None:
    """The ON cutoff in force for *channel*, or ``None`` when there is none.

    Goes through ``on_cutoff_for_column``, the **one resolver**, and hands it
    the column this panel actually draws — the channel's mean-intensity column.
    Two earlier attempts reached past it: first by reading
    ``thresh_frac_on.get(channel.lower())`` here, then by delegating to the raw
    ``get_thresh_frac_on`` accessor. ``tests/test_on_cutoff_sweep.py`` refuses
    both, and it is right to: there are three ON cutoffs because there are
    three scales, and a path that picks for itself is how a spot tally came to
    be measured against a fluorescence threshold that sits above its entire
    range. The panel drawing intensities does want ThreshFracOn — but that is
    the resolver's answer to give, not this function's to assume.

    ``None`` only for a cutoff of zero, which is "no cut" — the nuclear
    channel's default, among others — and a line on the axis edge claiming to
    be a threshold is worse than no line.
    """
    from well_viewer.core.channel_state_controller import on_cutoff_for_column

    token = str(channel or "").strip().lower()
    try:
        value = float(on_cutoff_for_column(
            ctx, f"{token}_mean_intensity", channel=token))
    except (TypeError, ValueError):
        return None
    return value if value else None


def _cell_gating_plot_cdf(ctx) -> None:
    ratio_data = ctx.runtime.cell_gating.ratio_data or {}
    if (not ctx.runtime.cell_gating.cell_areas
            and not ctx.runtime.cell_gating.fluor_data
            and not ratio_data):
        # No data — paint a themed empty state instead of leaving the default
        # white matplotlib canvas (a giant white block against the dark app).
        fig = ctx.runtime.cell_gating.figure
        if fig is not None:
            fig.clf()
            fig.set_facecolor(theme.Colors.surface)
            ax = fig.add_subplot(111, facecolor=theme.Colors.panel)
            ax.set_axis_off()
            ax.text(
                0.5, 0.5,
                "No dataset loaded.\nOpen a results folder to view cell-gating "
                "distributions.",
                transform=ax.transAxes, ha="center", va="center",
                color=theme.Colors.text_muted, fontsize=11, wrap=True,
            )
            canvas = ctx.runtime.cell_gating.canvas
            if canvas is not None:
                canvas.draw_idle()
        return

    bg_app = theme.Colors.surface
    bg_panel = theme.Colors.panel
    txt_pri = theme.Colors.text_primary
    txt_mut = theme.Colors.text_muted
    accent = theme.Colors.accent
    warn = theme.Colors.warn

    fig = ctx.runtime.cell_gating.figure
    fig.clf()
    fig.set_facecolor(bg_app)

    n_plots = 1 + len(ctx.runtime.cell_gating.fluor_data) + len(ratio_data)
    n_cols = 1 if n_plots == 1 else 2
    n_rows = (n_plots + n_cols - 1) // n_cols
    plot_height_per_row = 3.8
    fig_height = max(5.0, n_rows * plot_height_per_row)

    axes = []
    for i in range(n_plots):
        ax = fig.add_subplot(n_rows, n_cols, i + 1, facecolor=bg_panel)
        axes.append(ax)

    if ctx.runtime.cell_gating.cell_areas:
        areas = np.array(sorted(ctx.runtime.cell_gating.cell_areas))
        cdf = np.arange(1, len(areas) + 1) / len(areas)
        axes[0].plot(areas, cdf, linewidth=2, color=accent, alpha=0.8)
        axes[0].fill_between(areas, cdf, alpha=0.2, color=accent)
        axes[0].set_xlabel("Cell Area (pixels)", color=txt_pri, fontsize=9)
        axes[0].set_ylabel("Cumulative Probability", color=txt_pri, fontsize=9)
        axes[0].set_title("Cell Area Distribution", color=txt_pri,
                          fontsize=10, fontweight="bold")
        axes[0].grid(True, alpha=0.2, color=txt_mut)
        axes[0].tick_params(colors=txt_mut, labelsize=8)

        # Mark the QC layer's min-area gate (cell-area gating is QC-owned now).
        try:
            from well_viewer.features.qc.controller import get_qc_params
            _qc_min = get_qc_params(ctx).min_area
            if _qc_min is not None:
                axes[0].axvline(x=float(_qc_min), color=warn,
                                linestyle="--", linewidth=2, alpha=0.7)
        except Exception:
            swallowed()  # was: pass (silent except)
        _focus_cdf_xaxis(axes[0], areas)

    colors = theme.WELL_COLORS_TUPLE
    next_idx = 1
    for channel, values in sorted(ctx.runtime.cell_gating.fluor_data.items()):
        if next_idx >= len(axes):
            break
        ax = axes[next_idx]
        color = colors[next_idx % len(colors)]
        next_idx += 1
        vals = np.array(sorted(values))
        if vals.size == 0:
            continue  # empty channel → no CDF, but the axes slot stays reserved
                       # (audit S8; avoids 0/0) so later channels don't inherit it
        cdf = np.arange(1, len(vals) + 1) / len(vals)
        ax.plot(vals, cdf, linewidth=2, color=color, alpha=0.8)
        ax.fill_between(vals, cdf, alpha=0.2, color=color)
        ax.set_xlabel(f"{channel.upper()} Intensity", color=txt_pri, fontsize=9)
        ax.set_ylabel("Cumulative Probability", color=txt_pri, fontsize=9)
        ax.set_title(f"{channel.upper()} Distribution", color=txt_pri,
                     fontsize=10, fontweight="bold")
        ax.grid(True, alpha=0.2, color=txt_mut)
        ax.tick_params(colors=txt_mut, labelsize=8)

        # No gate for this channel means no line to draw — an ordinary branch,
        # not a suppressed failure. ``swallowed()`` here logged one anyway, with
        # ``exc_info`` and no exception in flight, so the record read as
        # something having gone wrong on a channel that is simply ungated.
        fluor_gate = ctx.state.cell_gating.fluor_gates.get(channel)
        if fluor_gate is not None:
            ax.axvline(x=float(fluor_gate), color=warn,
                       linestyle="--", linewidth=2, alpha=0.7)
        # The ON cutoff, beside the gate it is constantly confused with. These
        # CDFs are the surface for choosing it — the whole optimiser above them
        # searches for it — and it was the one line not on them: only
        # FluorGating was drawn, so a user setting ThreshFracOn had no way to
        # see where it fell on the distribution. Drawn as the *effective*
        # value, defaults included, because that is the number the fraction
        # panel everywhere else divides by.
        on_cutoff = _effective_thresh_frac_on(ctx, channel)
        if on_cutoff is not None:
            ax.axvline(x=float(on_cutoff), color=theme.Colors.accent,
                       linestyle=":", linewidth=2, alpha=0.9)
        _focus_cdf_xaxis(ax, vals)

    for ratio_label, values in sorted(ratio_data.items()):
        if next_idx >= len(axes):
            break
        ax = axes[next_idx]
        color = colors[next_idx % len(colors)]
        next_idx += 1
        vals = np.array(sorted(values))
        if vals.size == 0:
            continue  # empty ratio → no CDF, but the axes slot stays reserved
                      # (audit S8; avoids 0/0) so later ratios don't inherit it
        cdf = np.arange(1, len(vals) + 1) / len(vals)
        ax.plot(vals, cdf, linewidth=2, color=color, alpha=0.8)
        ax.fill_between(vals, cdf, alpha=0.2, color=color)
        ax.set_xlabel(f"{ratio_label} ratio", color=txt_pri, fontsize=9)
        ax.set_ylabel("Cumulative Probability", color=txt_pri, fontsize=9)
        ax.set_title(f"{ratio_label} Distribution", color=txt_pri,
                     fontsize=10, fontweight="bold")
        ax.grid(True, alpha=0.2, color=txt_mut)
        ax.tick_params(colors=txt_mut, labelsize=8)
        _focus_cdf_xaxis(ax, vals)

    ctx.runtime.cell_gating.ax = axes[0]

    if not ctx.runtime.cell_gating.axes_stack:
        limits = [(ax.get_xlim(), ax.get_ylim()) for ax in axes]
        ctx.runtime.cell_gating.axes_stack.append(limits)

    fig.tight_layout(pad=1.3)
    ctx.runtime.cell_gating.canvas.draw_idle()

    # Constrain height only; width is driven by the scroll area's
    # setWidgetResizable(True) so the canvas fills the viewport.
    dpi = fig.get_dpi()
    fig_h_px = max(1, int(fig_height * dpi))
    ctx.runtime.cell_gating.canvas.setMinimumHeight(fig_h_px)
