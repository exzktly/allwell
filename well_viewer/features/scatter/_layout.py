"""Per-cell scatter layout: Overlay (single axis) vs Separate axes (one group
per stacked panel), mirroring the Distribution tab's faceting.

Kept out of ``controller.py`` (line budget) — the controller calls
:func:`_setup_scatter_axes` to collapse stale panels back to one axis and
:func:`_render_scatter_faceted` for the view-only separate-axes layout. The
draggable gate tool + click-to-view-cell stay on the Overlay path, so the
faceted renderer clears the interaction cache and never arms the gate selector.
"""

from __future__ import annotations

from core.safe import swallowed
from well_viewer.core.plot_annotations import value_label_settings
from well_viewer.core.plot_style import apply_export_style_to_current
from well_viewer.features.scatter._annotations import note_value_labels_unavailable
from well_viewer.features.scatter.collect import gate_filter_title_suffix
from well_viewer.features.scatter.gating import (
    paint_gates, refresh_gate_selector, render_scatter_points,
)

#: Minimum on-screen height (px) per stacked panel in "Separate axes" mode.
#: Below this, panels squish to unreadable slivers; instead the card grows
#: taller than its viewport and the surrounding scroll area scrolls.
SCATTER_PANEL_MIN_PX = 260


def log1p_axis_suffix(on: bool) -> str:
    """Axis-label suffix noting the log10(value+1) display transform."""
    return "  (log₁₀, +1)" if on else ""


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


def _style_scatter_ax(ctx, ax, fig=None):
    """Apply Screen/Publication theme tokens to one scatter axis. Returns the
    ``(bg, title_fg, muted_fg, grid, spine)`` tuple for the caller.

    *fig* is the figure whose facecolor should track the theme bg; it defaults
    to the single / faceted scatter figure (``_scatter_fig``). The aggregate
    scatter passes ``_scatter_agg_fig``. (audit A16 — this was re-inlined at both
    scatter/controller.py call sites.)"""
    from well_viewer.core.plot_style import tokens_for as _tokens_for_ax
    _bg, _title_fg, _muted_fg, _grid, _spine = _tokens_for_ax(ax)
    if fig is None:
        fig = ctx.widgets.get_or_none("_scatter_fig")
    try:
        fig.set_facecolor(_bg)
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


def _setup_scatter_axes(ctx, n_axes: int):
    """Reconfigure the scatter figure to host *n_axes* stacked panels (shared x
    and y so the clouds are directly comparable). Reuses existing axes when the
    count matches; otherwise rebuilds. ``_ax_scatter`` is aliased to the first
    axis for the overlay path + gate tooling. Mirrors the Distribution tab's
    ``_setup_axes``."""
    from well_viewer.core.plot_style import reconfigure_stacked_axes

    def _build(fig):
        if n_axes == 1:
            axes = [fig.add_subplot(1, 1, 1)]
            fig.subplots_adjust(hspace=0.3, top=0.95, bottom=0.12, left=0.16, right=0.97)
        else:
            # Wider left margin so the per-panel y-axis label clears the figure's
            # left edge — in publication mode (larger fonts + log tick labels) a
            # tight 0.12 margin pushed the label off-canvas, where it was clipped
            # at render and dropped from the per-panel "save to gallery" crop
            # (get_tightbbox only captures artists inside the figure).
            axes = list(fig.subplots(n_axes, 1, sharex=True, sharey=True))
            fig.subplots_adjust(top=0.95, bottom=0.10, left=0.16, right=0.97, hspace=0.08)
        return axes

    axes = reconfigure_stacked_axes(ctx.widgets.get_or_none("_scatter_fig"), n_axes, _build)
    ctx.widgets.register("_ax_scatter", axes[0])
    ctx.runtime.scatter.axes = axes
    # Keep a reasonable minimum height per panel so stacked panels don't squish:
    # the card grows downward and the scroll area scrolls (overlay = no minimum).
    card = ctx.widgets.get_or_none("_scatter_card")
    if card is not None:
        try:
            card.setMinimumHeight(n_axes * SCATTER_PANEL_MIN_PX if n_axes > 1 else 0)
        except Exception:
            swallowed()
    return axes


def _render_scatter_faceted(ctx, scatter_data, col_x, col_y, tps_list) -> None:
    """View-only stacked layout: one scatter panel per group, axes shared.

    Gates and click-to-view-cell are overlay-only, so this path renders points
    (with density shading if enabled) and clears the interaction cache.
    """
    items = list(scatter_data.items())
    axes = _setup_scatter_axes(ctx, len(items))
    prefs = ctx.state.export_prefs.style_prefs or {}
    density_on = bool(prefs.get("scatter_density", False))
    x_log = bool(prefs.get("scatter_x_log", True))
    y_log = bool(prefs.get("scatter_y_log", True))
    marker_size = float(prefs.get("marker_size", 5.0) or 5.0)
    # "Color by time" colours each panel's points by timepoint (see
    # render_scatter_points); each panel then legends the timepoints it shows.
    color_by_tp = bool(ctx.state.scatter.color_by_tp)
    xlabel = _scatter_col_label(col_x) + log1p_axis_suffix(x_log)
    ylabel = _scatter_col_label(col_y) + log1p_axis_suffix(y_log)

    gate_entries = []
    axes_points = []
    for ax, (label, data) in zip(axes, items):
        _bg, _title_fg, _muted_fg, _grid, _spine = _style_scatter_ax(ctx, ax)
        _ipts, legend_handles = render_scatter_points(
            ctx, ax, {label: data}, density_on=density_on,
            x_log=x_log, y_log=y_log, marker_size=marker_size,
            color_by_tp=color_by_tp,
        )
        # Remember each panel's click-interaction points alongside its axis so
        # on_scatter_click can resolve which panel was clicked (shared x/y axes
        # make a single global nearest-search ambiguous across panels).
        axes_points.append((ax, _ipts))
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3, color=_grid)
        if legend_handles:
            ax.legend(handles=legend_handles, loc="best", fontsize=8,
                      framealpha=0.0, facecolor="none")
        else:
            ax.legend(loc="best", fontsize=8, framealpha=0.0, facecolor="none")
        # Gates appear on every panel, each labelled with that group's own
        # in-gate %. Handles only on the top panel — the one the edge-drag
        # editor is bound to (_ax_scatter); a drag there repaints them all.
        gate_entries.append((ax, {label: data}, tps_list, _muted_fg, ax is axes[0]))
    # The stacked panels carry no titles (each is identified by its legend), so
    # the gate-filtered tag rides the one shared label at the foot of the stack.
    # It has to appear somewhere: the toggle changes which cells are plotted,
    # and a figure saved out of here must say so without the on-screen control.
    axes[-1].set_xlabel(xlabel + gate_filter_title_suffix(ctx))
    paint_gates(ctx, gate_entries, cols=(col_x, col_y))

    # One note for the whole stack, on the top panel: the value-label overlay
    # states a statistic and every panel here draws one point per cell. Saying
    # it once per panel would be the same sentence four times over.
    note_value_labels_unavailable(value_label_settings(ctx), axes[0])

    # Editing the gate on the top panel mirrors to the panels below. Click- and
    # hover-to-view-cell now work per panel: ``axes_points`` pairs each panel's
    # points with its axis; ``points`` is the flattened union kept for callers
    # that don't care which panel a point came from.
    flat_points = [pt for _ax, pts in axes_points for pt in pts]
    ctx.runtime.scatter.interaction_cache = {
        "points": flat_points,
        "axes_points": axes_points,
        "timepoint_h": tps_list[0] if len(tps_list) == 1 else None,
        "timepoints_h": tps_list,
        "col_x": col_x,
        "col_y": col_y,
    }
    canvas = ctx.widgets.get_or_none("_scatter_canvas")
    try:
        apply_export_style_to_current(
            ctx, ctx.widgets.get_or_none("_scatter_fig"), canvas)
    except Exception:
        swallowed()
    # Re-arm the gate selector against the new top panel (axes[0]). The figure
    # was just rebuilt, so a previously-armed selector is bound to a dead axis;
    # mirror the overlay redraw's refresh so drawing in "Separate axes" mode
    # captures coordinates on the live panel. No-op when draw mode is off.
    refresh_gate_selector(ctx)
    if canvas is not None:
        canvas.draw()
