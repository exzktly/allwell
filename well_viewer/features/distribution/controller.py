"""Distribution tab rendering.

Histograms / KDE / violin plots of the active value column at a chosen
timepoint, grouped per replicate set or per well. The active column is
ratio-aware (handled transparently by ``data_loading.resolve_value_series``).
"""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

import numpy as np

from well_viewer.data.data_loading import _all_fluor_values_filtered, iter_plot_groups
from well_viewer.core.safe import swallowed
from well_viewer.core.plot_style import apply_export_style_to_current


NO_DATA_MSG = (
    "No values to plot.\n"
    "Pick a channel, metric, and timepoint, and select wells or groups."
)


def _gaussian_kde(values: Sequence[float], grid: np.ndarray) -> np.ndarray:
    """Silverman's-rule Gaussian KDE evaluated on *grid*.

    Hand-rolled fallback so the distribution tab works without scipy. Returns
    an array of the same shape as *grid*.
    """
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = arr.size
    if n < 2:
        return np.zeros_like(grid)
    sd = float(np.std(arr, ddof=1))
    if sd <= 0.0:
        return np.zeros_like(grid)
    iqr = float(np.subtract(*np.percentile(arr, [75, 25])))
    sigma = min(sd, iqr / 1.34) if iqr > 0 else sd
    h = 0.9 * sigma * (n ** (-1.0 / 5.0))
    if h <= 0.0:
        return np.zeros_like(grid)
    diff = (grid[:, None] - arr[None, :]) / h
    kernel = np.exp(-0.5 * diff * diff) / math.sqrt(2.0 * math.pi)
    return kernel.sum(axis=1) / (n * h)


def _grid_for(values_lists: Iterable[Sequence[float]], n_pts: int = 200, log_x: bool = False) -> np.ndarray:
    """Build an x-axis grid spanning all per-group min/max."""
    chunks = [np.asarray(vs, dtype=float) for vs in values_lists]
    if not chunks:
        return np.linspace(0.0, 1.0, n_pts)
    pooled = np.concatenate(chunks)
    pooled = pooled[np.isfinite(pooled)]
    if pooled.size == 0:
        return np.linspace(0.0, 1.0, n_pts)
    lo = float(pooled.min())
    hi = float(pooled.max())
    if hi <= lo:
        hi = lo + 1.0
    if log_x and lo > 0:
        return np.logspace(math.log10(lo), math.log10(hi), n_pts)
    return np.linspace(lo, hi, n_pts)


def _empty_msg(ax) -> None:
    # Preserve the facecolor that the caller already applied via
    # apply_axes_style; matplotlib's ax.clear() resets it otherwise.
    fc = ax.get_facecolor()
    ax.clear()
    ax.set_facecolor(fc)
    fig = ax.figure
    if fig is not None:
        fig.set_facecolor(fc)
    ax.text(
        0.5, 0.5, NO_DATA_MSG,
        ha="center", va="center", transform=ax.transAxes,
        fontsize=10, color="gray",
    )
    ax.set_axis_off()


def _apply_export_style(app) -> None:
    """Push the user's Export Style prefs (font sizes, grid, limits, …) onto
    the distribution figure. Called at the end of each redraw so toggling
    grid/log/etc. in the sidebar configurator sticks across re-renders.
    """
    fig = getattr(app, "_distribution_fig", None)
    canvas = getattr(app, "_distribution_canvas", None)
    if fig is None:
        return
    try:
        apply_export_style_to_current(app, fig, canvas)
    except Exception:
        swallowed()  # was: pass (silent except)


def _apply_card_style(app, ax) -> None:
    """Re-apply the active card's plot theme to *ax* after ``ax.clear()``.

    The other plot tabs route through ``well_viewer.plot_style.apply_ax_style``
    which already does this; the distribution renderer didn't, so on initial
    load the dark Screen-mode tokens never reached the axes (the user saw
    matplotlib's default white background instead).
    """
    from widgets.plot_card import apply_axes_style
    card = getattr(app, "_distribution_card", None)
    mode = getattr(card, "_plot_theme", "screen") if card is not None else "screen"
    apply_axes_style(ax, mode)


def _shared_bin_edges(
    values_lists: Iterable[Sequence[float]], bins: int, log_x: bool,
) -> np.ndarray:
    """Compute a single set of bin edges spanning every group's data.

    Histograms previously called ``ax.hist(vals, bins=N)`` per group;
    matplotlib then picked edges from each group's own min/max, so the
    bars across groups didn't line up and comparison was meaningless.
    Pool every group's values once, derive ``bins + 1`` edges from the
    combined range, then pass the edge array to every per-group hist
    call so the same x-axis bins are shared across groups.
    """
    chunks = [np.asarray(vs, dtype=float) for vs in values_lists]
    if not chunks:
        return np.linspace(0.0, 1.0, max(2, bins) + 1)
    pooled = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    pooled = pooled[np.isfinite(pooled)]
    n_edges = max(2, int(bins)) + 1
    if pooled.size == 0:
        return np.linspace(0.0, 1.0, n_edges)
    lo = float(pooled.min())
    hi = float(pooled.max())
    if hi <= lo:
        hi = lo + 1.0
    if log_x and lo > 0:
        return np.logspace(math.log10(lo), math.log10(hi), n_edges)
    return np.linspace(lo, hi, n_edges)


def _setup_axes(app, n_axes: int):
    """Reconfigure the distribution figure to host *n_axes* stacked subplots.

    Reuses the existing axes when the count already matches (avoids the
    expensive ``fig.clear()`` + figure-canvas resize on every redraw);
    otherwise wipes the figure and lays out ``n_axes`` stacked subplots
    with ``hspace=0.02`` so the faceted layout reads as one connected
    chart instead of a list of separate panels. The first axis is
    aliased back onto ``app._distribution_ax`` for back-compat with
    older code paths that pluck the single axis directly.
    """
    fig = app._distribution_fig
    existing = list(fig.axes)
    if len(existing) == n_axes:
        for ax in existing:
            ax.clear()
        axes = existing
    else:
        # ``fig.clear()`` resets figure-level attributes, which can wipe
        # the ``_plot_card`` back-ref that ``plot_style.tokens_for``
        # consults — re-attach it after rebuilding axes so the next
        # ``_apply_card_style`` reads the right palette.
        card = getattr(fig, "_plot_card", None)
        fig.clear()
        if card is not None:
            try:
                fig._plot_card = card
            except Exception:
                swallowed()  # was: pass (silent except)
        if n_axes == 1:
            axes = [fig.add_subplot(1, 1, 1)]
            fig.subplots_adjust(top=0.93, bottom=0.12, left=0.10, right=0.97)
        else:
            axes = list(fig.subplots(n_axes, 1, sharex=True))
            # Minimal vertical padding so the stacked histograms read as
            # one chart rather than ``n`` disconnected panels; the
            # outer margins leave enough room for a suptitle + x-label.
            fig.subplots_adjust(
                top=0.93, bottom=0.10, left=0.10, right=0.97, hspace=0.02,
            )
    for ax in axes:
        _apply_card_style(app, ax)
    app._distribution_ax = axes[0]
    app._distribution_axes = axes
    return axes


def redraw_distribution(app) -> None:
    """Redraw the Distribution tab."""
    canvas = getattr(app, "_distribution_canvas", None)
    fig = getattr(app, "_distribution_fig", None)
    if canvas is None or fig is None:
        return

    val_col = app._app_state.channel.active_val_col
    fluor_gates = app._get_all_fluor_gates()
    ratios = app._app_state.channel.ratio_index

    tp_str = (getattr(app, "_distribution_tp_var", None).currentText()
              if getattr(app, "_distribution_tp_var", None) is not None else "")
    try:
        tp_h: float = float(tp_str) if tp_str not in ("", "—", None) else float("nan")
    except (ValueError, TypeError):
        tp_h = float("nan")

    mode = (getattr(app, "_distribution_mode_var", None).currentText()
            if getattr(app, "_distribution_mode_var", None) is not None
            else "Histogram + KDE")
    bins = int(getattr(app, "_distribution_bins", 40) or 40)
    log_x = bool(getattr(app, "_distribution_log_x", False))
    log_y = bool(getattr(app, "_distribution_log_y", False))
    layout_choice = (
        getattr(app, "_distribution_layout_var", None).currentText()
        if getattr(app, "_distribution_layout_var", None) is not None
        else "Overlay"
    )

    groups: List[Tuple[str, str, np.ndarray]] = []
    for name, color, df in iter_plot_groups(app, fallback_to_all=False):
        tp_filter = tp_h if math.isfinite(tp_h) else None
        vals = _all_fluor_values_filtered(
            df, val_col=val_col,
            fluor_gates=fluor_gates,
            ratios=ratios,
            tp_filter=tp_filter,
        )
        if vals.size:
            # Legend / violin / facet labels use the sample-definition display
            # label when one is defined (the raw name stays the CSV identity in
            # iter_plot_groups, which the export path keys on).
            groups.append((_display_label_for(app, name), color, vals))

    if not groups:
        axes = _setup_axes(app, 1)
        _empty_msg(axes[0])
        _apply_export_style(app)
        canvas.draw_idle()
        return

    # Faceted layout only makes sense for histogram modes — KDE-only
    # curves are thin and read fine overlaid, CDF curves are monotone
    # so overlap isn't an issue, and Violin already groups visually by
    # its own design. Other modes silently ignore the layout combo.
    is_faceted = (
        layout_choice.startswith("Faceted")
        and mode in ("Histogram", "Histogram + KDE")
        and len(groups) > 1
    )

    if is_faceted:
        _render_faceted_histograms(app, groups, mode, bins, log_x, tp_h, log_y=log_y)
    else:
        axes = _setup_axes(app, 1)
        ax = axes[0]
        ax.set_axis_on()
        _render_overlay(app, ax, groups, mode, bins, log_x, tp_h, log_y=log_y)

    # Re-apply theme text colours after the renderers set titles/labels.
    # `apply_axes_style` is called inside `_setup_axes` *before* the title
    # exists; matplotlib then created the title text object at its
    # default dark-grey colour, which read as low-contrast against the
    # dark plot bg (and broke entirely after the H20 palette reconcile).
    _retint_axes_text(app)
    _apply_export_style(app)
    canvas.draw_idle()


def _display_label_for(app, name: str) -> str:
    """Resolve the legend display label for an ``iter_plot_groups`` name.

    ``iter_plot_groups`` yields the raw identity — a well token (when no
    replicate sets are active) or a replicate-set name. A user-defined
    sample-definition label, when present, should show in the legend instead:
    a well token resolves through ``_well_display_label``; a replicate-set name
    resolves through ``_replicate_display_label`` (group name, with a well-label
    fallback for generic names). This mirrors what the Line / Bar plots already
    do. The raw ``name`` is left untouched in ``iter_plot_groups`` because the
    CSV export path keys its columns on it.
    """
    try:
        if name in (app._app_state.dataset.well_paths or {}):
            return app._well_display_label(name)
        for rset in app._rep_sets_active():
            if getattr(rset, "name", None) == name:
                return app._replicate_display_label(rset)
    except Exception:
        swallowed()  # was: pass (silent except)
    return name


def _retint_axes_text(app) -> None:
    """Recolour the title + axis labels of every axis on the distribution
    figure using the active card's plot theme. Run after the renderers
    so titles set via ``ax.set_title(...)`` pick up the right ink.
    """
    fig = getattr(app, "_distribution_fig", None)
    if fig is None:
        return
    from widgets.plot_card import plot_tokens
    card = getattr(app, "_distribution_card", None)
    mode = getattr(card, "_plot_theme", "screen") if card is not None else "screen"
    _bg, fg, _muted, _grid, _spine = plot_tokens(mode)
    import theme as _theme
    sec = fg if mode == "publication" else _theme.Colors.text_secondary
    for ax in fig.axes:
        try:
            if ax.get_title():
                ax.title.set_color(fg)
            ax.xaxis.label.set_color(sec)
            ax.yaxis.label.set_color(sec)
        except Exception:
            swallowed()  # was: pass (silent except)
    # Faceted-mode uses a figure-level suptitle, not per-axis titles —
    # without this it kept matplotlib's default black ink and disappeared
    # into the dark screen-mode background.
    suptitle = getattr(fig, "_suptitle", None)
    if suptitle is not None:
        try:
            suptitle.set_color(fg)
        except Exception:
            swallowed()  # was: pass (silent except)


def _render_overlay(app, ax, groups, mode, bins, log_x, tp_h, *, log_y=False) -> None:
    """Single-axis renderer covering every mode (Hist / Hist+KDE / KDE / CDF / Violin)."""
    if mode == "Violin (per group)":
        data = [vals for _, _, vals in groups]
        labels = [name for name, _, _ in groups]
        parts = ax.violinplot(data, showmeans=False, showmedians=True)
        for i, body in enumerate(parts.get("bodies", [])):
            body.set_facecolor(groups[i][1])
            body.set_edgecolor(groups[i][1])
            body.set_alpha(0.55)
        ax.set_xticks(range(1, len(labels) + 1))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        ax.set_ylabel("Value")
    elif mode == "CDF":
        for name, color, vals in groups:
            arr = np.asarray(vals, dtype=float)
            arr = arr[np.isfinite(arr)]
            n = arr.size
            if n == 0:
                continue
            xs = np.sort(arr)
            ys = np.arange(1, n + 1, dtype=float) / float(n)
            ax.step(
                xs, ys, where="post", color=color, lw=1.6,
                label=f"{name} (n={n:,})",
            )
        if log_x:
            try:
                ax.set_xscale("log")
            except Exception:
                swallowed()  # was: pass (silent except)
        ax.set_xlabel(_xlabel_for(app))
        ax.set_ylabel("Cumulative fraction")
        if log_y:
            # A log y-axis can't include the 0/negative padding the linear CDF
            # uses; let matplotlib autoscale the positive cumulative fractions.
            try:
                ax.set_yscale("log")
            except Exception:
                swallowed()
        else:
            ax.set_ylim(-0.02, 1.05)
        if any(name for name, _, _ in groups):
            try:
                ax.legend(fontsize=7, loc="best", framealpha=0.0, facecolor="none")
            except Exception:
                swallowed()  # was: pass (silent except)
    else:
        # Histogram / Histogram + KDE / KDE only — shared bin edges so
        # every group's bars line up on the same x-grid.
        edges = _shared_bin_edges((vs for _, _, vs in groups), bins, log_x)
        grid = _grid_for((vs for _, _, vs in groups), log_x=log_x)
        for name, color, vals in groups:
            if mode in ("Histogram", "Histogram + KDE"):
                ax.hist(
                    vals, bins=edges, density=True, alpha=0.45,
                    color=color, label=f"{name} (n={int(vals.size)})",
                )
            if mode in ("KDE only", "Histogram + KDE"):
                kde = _gaussian_kde(vals, grid)
                if np.any(kde > 0):
                    ax.plot(grid, kde, color=color, lw=1.6,
                            label=None if mode == "Histogram + KDE" else f"{name} (n={len(vals)})")
        if log_x:
            try:
                ax.set_xscale("log")
            except Exception:
                swallowed()  # was: pass (silent except)
        if log_y:
            try:
                ax.set_yscale("log")
            except Exception:
                swallowed()
        ax.set_xlabel(_xlabel_for(app))
        ax.set_ylabel("Density")
        if any(name for name, _, _ in groups):
            try:
                ax.legend(fontsize=7, loc="best", framealpha=0.0, facecolor="none")
            except Exception:
                swallowed()  # was: pass (silent except)

    # Threshold marker for non-violin modes — drawn for intensities and now
    # for smFISH counts too (ThreshFracOn is the spot-count cutoff). Shape
    # metrics have no per-channel cutoff, so they fall out via is_shape_active.
    if (mode != "Violin (per group)" and app._app_state.plot.show_threshold_line
            and not app._app_state.channel.is_shape_active()):
        try:
            threshold = app._get_thresh_frac_on(app._app_state.channel.active_channel)
            if math.isfinite(threshold):
                ax.axvline(threshold, color="orange", linestyle="--", linewidth=1.0, alpha=0.8)
        except Exception:
            swallowed()  # was: pass (silent except)

    title = _title_for(app, tp_h, int(sum(int(vs.size) for _, _, vs in groups)), len(groups))
    ax.set_title(title, fontsize=9)


def _render_faceted_histograms(app, groups, mode, bins, log_x, tp_h, *, log_y=False) -> None:
    """One axis per group, stacked vertically with minimal padding.

    Shared x-axis (``sharex=True`` via ``_setup_axes``) + shared bin
    edges means every panel reads on the same grid; the user can scan
    vertically to compare distributions without per-axis re-binning
    differences swamping the comparison.
    """
    axes = _setup_axes(app, len(groups))
    edges = _shared_bin_edges((vs for _, _, vs in groups), bins, log_x)
    grid = _grid_for((vs for _, _, vs in groups), log_x=log_x)

    # Shapes have no ON cutoff → no threshold line. smFISH counts now draw
    # the ThreshFracOn line like intensities.
    if app._app_state.channel.is_shape_active():
        threshold = float("nan")
    else:
        try:
            threshold = app._get_thresh_frac_on(app._app_state.channel.active_channel)
        except Exception:
            threshold = float("nan")
    show_threshold = math.isfinite(threshold) and app._app_state.plot.show_threshold_line

    last_idx = len(groups) - 1
    for i, ((name, color, vals), ax) in enumerate(zip(groups, axes)):
        ax.set_axis_on()
        if mode in ("Histogram", "Histogram + KDE"):
            ax.hist(
                vals, bins=edges, density=True, alpha=0.55,
                color=color,
            )
        if mode == "Histogram + KDE":
            kde = _gaussian_kde(vals, grid)
            if np.any(kde > 0):
                ax.plot(grid, kde, color=color, lw=1.4)
        if show_threshold:
            ax.axvline(threshold, color="orange", linestyle="--", linewidth=1.0, alpha=0.8)
        if log_x:
            try:
                ax.set_xscale("log")
            except Exception:
                swallowed()  # was: pass (silent except)
        if log_y:
            try:
                ax.set_yscale("log")
            except Exception:
                swallowed()
        # Annotate group name on the right edge of each panel so the
        # vertical stack reads top-to-bottom without consuming a slot
        # in a legend or stretching the layout horizontally.
        ax.text(
            0.99, 0.92, f"{name} (n={int(vals.size):,})",
            transform=ax.transAxes,
            ha="right", va="top", fontsize=8, color=color,
            fontweight="bold",
        )
        # Trim per-axis chrome — hspace=0.02 means consecutive axes touch,
        # so the top axes shouldn't show x-tick labels (handled by
        # ``sharex=True``) and intermediate y-tick labels can stay since
        # density values differ per group.
        if i != last_idx:
            ax.tick_params(axis="x", labelbottom=False)
    # Shared x-label on the bottom axis only.
    axes[last_idx].set_xlabel(_xlabel_for(app))
    # Single shared y-label across the middle axis; "Density" is the
    # same unit for every panel because each hist uses density=True.
    axes[len(axes) // 2].set_ylabel("Density")
    # Use a figure-level suptitle so the title isn't duplicated per axis.
    fig = app._distribution_fig
    title = _title_for(
        app, tp_h, int(sum(int(vs.size) for _, _, vs in groups)), len(groups),
    )
    fig.suptitle(title, fontsize=9)


def _xlabel_for(app) -> str:
    label = app._active_channel_label() if hasattr(app, "_active_channel_label") else app._app_state.channel.active_channel.upper()
    metric = app._app_state.channel.active_metric
    if metric and not str(app._app_state.channel.active_val_col).startswith("ratio:"):
        return f"{label} {metric.replace('_', ' ')}"
    return label


def _title_for(app, tp_h: float, n_cells: int, n_groups: int) -> str:
    label = app._active_channel_label() if hasattr(app, "_active_channel_label") else app._app_state.channel.active_channel.upper()
    if math.isfinite(tp_h):
        tp_str = f"t = {tp_h:g} h"
    else:
        tp_str = "all timepoints"
    return f"{label} distribution — {tp_str} ({n_cells} cells, {n_groups} groups)"
