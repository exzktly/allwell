"""Per-cell scatter layout: Overlay (single axis) vs Separate axes (one group
per stacked panel), mirroring the Distribution tab's faceting.

Kept out of ``controller.py`` (line budget) — the controller calls
:func:`_setup_scatter_axes` to collapse stale panels back to one axis and
:func:`_render_scatter_faceted` for the view-only separate-axes layout. The
draggable gate tool + click-to-view-cell stay on the Overlay path, so the
faceted renderer clears the interaction cache and never arms the gate selector.
"""

from __future__ import annotations

from well_viewer.core.safe import swallowed
from well_viewer.core.plot_style import apply_export_style_to_current
from well_viewer.features.scatter.gating import paint_gates, render_scatter_points

#: Minimum on-screen height (px) per stacked panel in "Separate axes" mode.
#: Below this, panels squish to unreadable slivers; instead the card grows
#: taller than its viewport and the surrounding scroll area scrolls.
SCATTER_PANEL_MIN_PX = 260


def _scatter_col_label(col: str) -> str:
    """Readable axis label for a metric/shape column."""
    from pipeline.shape_features import is_shape_column
    from well_viewer.data.metric_labels import split_metric_col
    if is_shape_column(col):
        from pipeline.shape_features import label_for as _shape_label_for
        return _shape_label_for(col) or col
    parts = split_metric_col(col)
    if parts is None:
        return col
    _ch, metric_key, label = parts
    if metric_key == "smfish_count":
        return f"{label.split(' ')[0]} smFISH Count"
    return label


def _style_scatter_ax(app, ax):
    """Apply Screen/Publication theme tokens to one scatter axis. Returns the
    ``(bg, title_fg, muted_fg, grid, spine)`` tuple for the caller."""
    from well_viewer.core.plot_style import tokens_for as _tokens_for_ax
    _bg, _title_fg, _muted_fg, _grid, _spine = _tokens_for_ax(ax)
    try:
        app._scatter_fig.set_facecolor(_bg)
    except Exception:
        swallowed()
    ax.set_facecolor(_bg)
    for sp in ax.spines.values():
        sp.set_color(_spine)
        sp.set_linewidth(0.8)
    ax.tick_params(colors=_muted_fg, labelsize=8)
    ax.xaxis.label.set_color(_muted_fg)
    ax.yaxis.label.set_color(_muted_fg)
    ax.title.set_color(_title_fg)
    return _bg, _title_fg, _muted_fg, _grid, _spine


def _setup_scatter_axes(app, n_axes: int):
    """Reconfigure the scatter figure to host *n_axes* stacked panels (shared x
    and y so the clouds are directly comparable). Reuses existing axes when the
    count matches; otherwise rebuilds. ``app._ax_scatter`` is aliased to the
    first axis for the overlay path + gate tooling. Mirrors the Distribution
    tab's ``_setup_axes``."""
    fig = app._scatter_fig
    existing = list(fig.axes)
    if len(existing) == n_axes:
        for ax in existing:
            ax.clear()
        axes = existing
    else:
        card = getattr(fig, "_plot_card", None)
        fig.clear()
        if card is not None:
            try:
                fig._plot_card = card
            except Exception:
                swallowed()
        if n_axes == 1:
            axes = [fig.add_subplot(1, 1, 1)]
            fig.subplots_adjust(hspace=0.3, top=0.95, bottom=0.12, left=0.12, right=0.97)
        else:
            axes = list(fig.subplots(n_axes, 1, sharex=True, sharey=True))
            fig.subplots_adjust(top=0.95, bottom=0.10, left=0.12, right=0.97, hspace=0.08)
    app._ax_scatter = axes[0]
    app._scatter_axes = axes
    # Keep a reasonable minimum height per panel so stacked panels don't squish:
    # the card grows downward and the scroll area scrolls (overlay = no minimum).
    card = getattr(app, "_scatter_card", None)
    if card is not None:
        try:
            card.setMinimumHeight(n_axes * SCATTER_PANEL_MIN_PX if n_axes > 1 else 0)
        except Exception:
            swallowed()
    return axes


def _render_scatter_faceted(app, scatter_data, col_x, col_y, tps_list) -> None:
    """View-only stacked layout: one scatter panel per group, axes shared.

    Gates and click-to-view-cell are overlay-only, so this path renders points
    (with density shading if enabled) and clears the interaction cache.
    """
    items = list(scatter_data.items())
    axes = _setup_scatter_axes(app, len(items))
    prefs = getattr(app, "_export_style_prefs", None) or {}
    density_on = bool(prefs.get("scatter_density", False))
    x_log = bool(prefs.get("scatter_x_log", True))
    y_log = bool(prefs.get("scatter_y_log", True))
    marker_size = float(prefs.get("marker_size", 5.0) or 5.0)
    # "Color by time" colours each panel's points by timepoint (see
    # render_scatter_points); each panel then legends the timepoints it shows.
    color_by_tp = bool(getattr(app, "_scatter_color_by_tp", False))
    xlabel = _scatter_col_label(col_x)
    ylabel = _scatter_col_label(col_y)

    gate_entries = []
    for ax, (label, data) in zip(axes, items):
        _bg, _title_fg, _muted_fg, _grid, _spine = _style_scatter_ax(app, ax)
        _ipts, legend_handles = render_scatter_points(
            app, ax, {label: data}, density_on=density_on,
            x_log=x_log, y_log=y_log, marker_size=marker_size,
            color_by_tp=color_by_tp,
        )
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3, color=_grid)
        if legend_handles:
            ax.legend(handles=legend_handles, loc="best", fontsize=8,
                      framealpha=0.0, facecolor="none")
        else:
            ax.legend(loc="best", fontsize=8, framealpha=0.0, facecolor="none")
        # Gates appear on every panel, each labelled with that group's own
        # in-gate %. Handles only on the top panel — the one the edge-drag
        # editor is bound to (app._ax_scatter); a drag there repaints them all.
        gate_entries.append((ax, {label: data}, tps_list, _muted_fg, ax is axes[0]))
    axes[-1].set_xlabel(xlabel)
    paint_gates(app, gate_entries, cols=(col_x, col_y))

    # Editing the gate on the top panel mirrors to the panels below; the
    # click-to-view-cell cache stays overlay-only.
    app._scatter_interaction_cache = {"points": []}
    try:
        apply_export_style_to_current(
            app, app._scatter_fig, getattr(app, "_scatter_canvas", None))
    except Exception:
        swallowed()
    app._scatter_canvas.draw()
