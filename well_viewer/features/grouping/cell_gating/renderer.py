"""Cell Gating figures: the per-channel cumulative-distribution panel.

Pure matplotlib. Draws into the figure held on ``ctx.runtime.cell_gating`` and
touches no widget beyond the canvas it repaints, so a CDF can be rendered and
snapshot-tested without building the tab. The optimizer's own diff curve is a
separate window and lives in :mod:`opt_curve_view`.
"""

from __future__ import annotations

import numpy as np

import theme
from well_viewer.core.safe import swallowed


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

    colors = [accent, "#FF9500", "#FF3B30", "#34C759"]
    next_idx = 1
    for channel, values in sorted(ctx.runtime.cell_gating.fluor_data.items()):
        if next_idx >= len(axes):
            break
        ax = axes[next_idx]
        color = colors[next_idx % len(colors)]
        vals = np.array(sorted(values))
        if vals.size == 0:
            continue  # empty channel → no CDF (audit S8; avoids 0/0)
        cdf = np.arange(1, len(vals) + 1) / len(vals)
        ax.plot(vals, cdf, linewidth=2, color=color, alpha=0.8)
        ax.fill_between(vals, cdf, alpha=0.2, color=color)
        ax.set_xlabel(f"{channel.upper()} Intensity", color=txt_pri, fontsize=9)
        ax.set_ylabel("Cumulative Probability", color=txt_pri, fontsize=9)
        ax.set_title(f"{channel.upper()} Distribution", color=txt_pri,
                     fontsize=10, fontweight="bold")
        ax.grid(True, alpha=0.2, color=txt_mut)
        ax.tick_params(colors=txt_mut, labelsize=8)

        fluor_gate = ctx.state.cell_gating.fluor_gates.get(channel)
        if fluor_gate is not None:
            ax.axvline(x=float(fluor_gate), color=warn,
                       linestyle="--", linewidth=2, alpha=0.7)
        else:
            swallowed("cell gating CDF: no fluor-gate line (channel not in slice)")
        _focus_cdf_xaxis(ax, vals)
        next_idx += 1

    for ratio_label, values in sorted(ratio_data.items()):
        if next_idx >= len(axes):
            break
        ax = axes[next_idx]
        color = colors[next_idx % len(colors)]
        vals = np.array(sorted(values))
        if vals.size == 0:
            continue  # empty channel → no CDF (audit S8; avoids 0/0)
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
        next_idx += 1

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
