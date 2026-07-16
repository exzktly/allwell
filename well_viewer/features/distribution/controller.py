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
from well_viewer.core.plot_style import (
    apply_export_style_to_current, figure_prefs, style_legend,
)
from well_viewer.core.channel_state_controller import (
    active_channel_label, get_all_fluor_gates, get_thresh_frac_on,
)


NO_DATA_MSG = (
    "No values to plot.\n"
    "Pick a channel, metric, and timepoint, and select wells or groups."
)


# ── Log-scale single source of truth ────────────────────────────────────────
# The Distribution tab has two controls for each axis' log scale: the on-plot
# "log x"/"log y" checkboxes and the Properties-sidebar "X log"/"Y log" toggles.
# The sidebar writes the per-figure axis pref (``x_log``/``y_log``) and the
# export-style pass applies it *last*, so it used to silently override whatever
# the on-plot checkbox / renderer set — the button looked dead / inverted. These
# helpers make that per-figure pref the single source of truth: both controls
# read and write it, and it drives both the calculation (bin edges / KDE grid)
# and the axis scale. ``ctx.state.distribution.log_*`` is kept mirrored so the
# existing view-state persistence is unchanged.

def get_distribution_axis_log(ctx, axis: str) -> bool:
    """Read the unified log flag for ``axis`` ("x"/"y") from the figure pref,
    falling back to the mirrored feature-state field (first load / migration)."""
    key = "x_log" if axis == "x" else "y_log"
    state_attr = "log_x" if axis == "x" else "log_y"
    fig = ctx.widgets.get_or_none("_distribution_fig")
    if fig is not None:
        prefs = figure_prefs(ctx, fig)
        # An explicitly-set pref wins; when unset, seed it from the mirrored
        # state so a persisted button choice survives the first redraw.
        if key in prefs._axis:  # type: ignore[attr-defined]
            return bool(prefs[key])
    return bool(getattr(ctx.state.distribution, state_attr, False))


def set_distribution_axis_log(ctx, axis: str, on: bool) -> None:
    """Write the unified log flag for ``axis`` to the figure pref and mirror it
    into the feature-state field (for view-state persistence)."""
    key = "x_log" if axis == "x" else "y_log"
    state_attr = "log_x" if axis == "x" else "log_y"
    setattr(ctx.state.distribution, state_attr, bool(on))
    fig = ctx.widgets.get_or_none("_distribution_fig")
    if fig is not None:
        figure_prefs(ctx, fig)[key] = bool(on)


def _sync_log_checkbox(ctx, widget_name: str, on: bool) -> None:
    """Reflect the unified log flag onto an on-plot checkbox without re-emitting
    its ``stateChanged`` (which would recurse into a redraw)."""
    cb = ctx.widgets.get_or_none(widget_name)
    if cb is None or bool(cb.isChecked()) == bool(on):
        return
    blocked = cb.blockSignals(True)
    try:
        cb.setChecked(bool(on))
    finally:
        cb.blockSignals(blocked)


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


def _apply_export_style(ctx) -> None:
    """Push the user's Export Style prefs (font sizes, grid, limits, …) onto
    the distribution figure. Called at the end of each redraw so toggling
    grid/log/etc. in the sidebar configurator sticks across re-renders.
    """
    fig = ctx.widgets.get_or_none("_distribution_fig")
    canvas = ctx.widgets.get_or_none("_distribution_canvas")
    if fig is None:
        return
    try:
        apply_export_style_to_current(ctx, fig, canvas)
    except Exception:
        swallowed()  # was: pass (silent except)


def _apply_card_style(ctx, ax) -> None:
    """Re-apply the active card's plot theme to *ax* after ``ax.clear()``.

    The other plot tabs route through ``well_viewer.plot_style.apply_ax_style``
    which already does this; the distribution renderer didn't, so on initial
    load the dark Screen-mode tokens never reached the axes (the user saw
    matplotlib's default white background instead).
    """
    from widgets.plot_card import apply_axes_style
    card = ctx.widgets.get_or_none("_distribution_card")
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


def _setup_axes(ctx, n_axes: int):
    """Reconfigure the distribution figure to host *n_axes* stacked subplots.

    Reuses the existing axes when the count already matches (avoids the
    expensive ``fig.clear()`` + figure-canvas resize on every redraw);
    otherwise wipes the figure and lays out ``n_axes`` stacked subplots
    with ``hspace=0.02`` so the faceted layout reads as one connected
    chart instead of a list of separate panels. The first axis is
    aliased back onto ``app._distribution_ax`` for back-compat with
    older code paths that pluck the single axis directly.
    """
    from well_viewer.core.plot_style import reconfigure_stacked_axes

    def _build(fig):
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
        return axes

    axes = reconfigure_stacked_axes(ctx.widgets.get("_distribution_fig"), n_axes, _build)
    for ax in axes:
        _apply_card_style(ctx, ax)
    # First axis aliased back onto the registry for older single-axis readers.
    ctx.widgets.register("_distribution_ax", axes[0])
    ctx.widgets.register("_distribution_axes", axes)
    return axes


def _sync_distribution_tp_to_focus(ctx) -> None:
    """Move the timepoint combo to the shared focus timepoint (N5) when one is
    set and present, so a timepoint chosen on another plotting tab (Heat Map)
    drives this redraw. Signals blocked so the silent sync never re-fires the
    combo handler. No-op when no focus is set."""
    from well_viewer.core.timepoint_sync import current_focus_tp, nearest_index
    focus = current_focus_tp(ctx)
    if focus is None:
        return
    cb = ctx.widgets.get_or_none("_distribution_tp_cb")
    if cb is None:
        return
    items = [cb.itemText(i) for i in range(cb.count())]
    tps = []
    for t in items:
        try:
            tps.append(float(t))
        except (ValueError, TypeError):
            tps.append(float("nan"))
    i = nearest_index(tps, focus)
    if i is not None and 0 <= i < cb.count() and cb.currentIndex() != i:
        blocked = cb.blockSignals(True)
        try:
            cb.setCurrentIndex(i)
        finally:
            cb.blockSignals(blocked)


def redraw_distribution(ctx) -> None:
    """Redraw the Distribution tab. A8 teardown: takes ``ctx`` — widgets via
    ``ctx.widgets``, state via ``ctx.state``, and every shared render leaf
    (get_all_fluor_gates / iter_plot_groups / the label + threshold + export-style
    leaves) is now ctx-native, so the renderer names no god-object."""
    canvas = ctx.widgets.get_or_none("_distribution_canvas")
    fig = ctx.widgets.get_or_none("_distribution_fig")
    if canvas is None or fig is None:
        return

    val_col = ctx.state.channel.active_val_col
    fluor_gates = get_all_fluor_gates(ctx)
    ratios = ctx.state.channel.ratio_index

    _sync_distribution_tp_to_focus(ctx)
    tp_cb = ctx.widgets.get_or_none("_distribution_tp_var")
    tp_str = tp_cb.currentText() if tp_cb is not None else ""
    try:
        tp_h: float = float(tp_str) if tp_str not in ("", "—", None) else float("nan")
    except (ValueError, TypeError):
        tp_h = float("nan")

    mode_cb = ctx.widgets.get_or_none("_distribution_mode_var")
    mode = mode_cb.currentText() if mode_cb is not None else "Histogram + KDE"
    bins = int(ctx.state.distribution.bins or 40)
    # Read the unified log flags from the per-figure axis pref (single source of
    # truth shared with the Properties sidebar), then reflect them back onto the
    # on-plot checkboxes so a sidebar toggle keeps the checkbox in lockstep. The
    # same values drive the calculation (bin edges / KDE grid) and the axis, so
    # bins are never computed in a different scale than the axis is drawn in.
    log_x = get_distribution_axis_log(ctx, "x")
    log_y = get_distribution_axis_log(ctx, "y")
    # Seed the figure pref + mirror the feature-state field from the resolved
    # value, so the authoritative export-style pass reads exactly what the
    # renderer draws (covers the migration case where an old session persisted
    # only ``state.log_*`` and the pref was still unset — otherwise the export
    # pass would force the axis linear against the button).
    set_distribution_axis_log(ctx, "x", log_x)
    set_distribution_axis_log(ctx, "y", log_y)
    _sync_log_checkbox(ctx, "_distribution_log_x_cb", log_x)
    _sync_log_checkbox(ctx, "_distribution_log_y_cb", log_y)
    layout_cb = ctx.widgets.get_or_none("_distribution_layout_var")
    layout_choice = layout_cb.currentText() if layout_cb is not None else "Overlay"

    groups: List[Tuple[str, str, np.ndarray]] = []
    for name, color, df in iter_plot_groups(ctx, fallback_to_all=False):
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
            groups.append((_display_label_for(ctx, name), color, vals))

    if not groups:
        axes = _setup_axes(ctx, 1)
        _empty_msg(axes[0])
        _apply_export_style(ctx)
        canvas.draw_idle()
        return

    # Faceted layout applies to the histogram modes and CDF — one panel per
    # group so overlapping curves don't obscure each other (CDF steps in
    # particular pile up illegibly once several groups share an axis). KDE-only
    # curves are thin and read fine overlaid, and Violin already groups
    # visually by its own design, so those two silently ignore the layout combo.
    is_faceted = (
        layout_choice.startswith("Faceted")
        and mode in ("Histogram", "Histogram + KDE", "CDF")
        and len(groups) > 1
    )

    if is_faceted:
        _render_faceted(ctx, groups, mode, bins, log_x, tp_h, log_y=log_y)
    else:
        axes = _setup_axes(ctx, 1)
        ax = axes[0]
        ax.set_axis_on()
        _render_overlay(ctx, ax, groups, mode, bins, log_x, tp_h, log_y=log_y)

    # Re-apply theme text colours after the renderers set titles/labels.
    # `apply_axes_style` is called inside `_setup_axes` *before* the title
    # exists; matplotlib then created the title text object at its
    # default dark-grey colour, which read as low-contrast against the
    # dark plot bg (and broke entirely after the H20 palette reconcile).
    _retint_axes_text(ctx)
    _apply_export_style(ctx)
    canvas.draw_idle()


def _display_label_for(ctx, name: str) -> str:
    """Resolve the legend display label for an ``iter_plot_groups`` name.

    ``iter_plot_groups`` yields the raw identity — a well token (when no
    replicate sets are active) or a replicate-set name. A user-defined
    sample-definition label, when present, should show in the legend instead:
    a well token resolves through ``well_display_label``; a replicate-set name
    resolves through ``replicate_display_label`` (group name, with a well-label
    fallback for generic names). This mirrors what the Line / Bar plots already
    do. The raw ``name`` is left untouched in ``iter_plot_groups`` because the
    CSV export path keys its columns on it.

    A8 teardown: pure ctx — the display-label leaves all take ``ctx`` now.
    """
    from well_viewer.domain.selections_model import (
        active_replicate_sets, replicate_display_label, well_display_label,
    )
    try:
        if name in (ctx.state.dataset.well_paths or {}):
            return well_display_label(ctx, name)
        for rset in active_replicate_sets(
                ctx.state.selection.selections, ctx.state.dataset.well_paths):
            if getattr(rset, "name", None) == name:
                return replicate_display_label(ctx, rset)
    except Exception:
        swallowed()  # was: pass (silent except)
    return name


def _retint_axes_text(ctx) -> None:
    """Recolour the title + axis labels of every axis on the distribution
    figure using the active card's plot theme. Run after the renderers
    so titles set via ``ax.set_title(...)`` pick up the right ink.
    """
    fig = ctx.widgets.get_or_none("_distribution_fig")
    if fig is None:
        return
    from widgets.plot_card import plot_tokens
    card = ctx.widgets.get_or_none("_distribution_card")
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


def _render_overlay(ctx, ax, groups, mode, bins, log_x, tp_h, *, log_y=False) -> None:
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
        ax.set_xlabel(_xlabel_for(ctx))
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
                style_legend(ax.legend(fontsize=7, loc="best", framealpha=0.0, facecolor="none"), ax)
            except Exception:
                swallowed()  # was: pass (silent except)
    else:
        # Histogram / Histogram + KDE / KDE only — shared bin edges so
        # every group's bars line up on the same x-grid.
        edges = _shared_bin_edges((vs for _, _, vs in groups), bins, log_x)
        grid = _grid_for((vs for _, _, vs in groups), log_x=log_x)
        for name, color, vals in groups:
            if mode in ("Histogram", "Histogram + KDE"):
                # Pass ``log=log_y`` into hist so matplotlib computes proper
                # log-scaled bars/limits. Calling ``set_yscale("log")`` after
                # drawing density bars from a 0 baseline (log(0) undefined) made
                # the bar heights render wrong — this is the correct entry point.
                ax.hist(
                    vals, bins=edges, density=True, alpha=0.45, log=log_y,
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
            # Redundant for the histogram modes (``hist(log=True)`` already set
            # it) but needed for KDE-only, and idempotent otherwise.
            try:
                ax.set_yscale("log")
            except Exception:
                swallowed()
        ax.set_xlabel(_xlabel_for(ctx))
        ax.set_ylabel("Density")
        if any(name for name, _, _ in groups):
            try:
                style_legend(ax.legend(fontsize=7, loc="best", framealpha=0.0, facecolor="none"), ax)
            except Exception:
                swallowed()  # was: pass (silent except)

    # Threshold marker for non-violin modes — drawn for intensities and now
    # for smFISH counts too (ThreshFracOn is the spot-count cutoff). Shape
    # metrics have no per-channel cutoff, so they fall out via is_shape_active.
    _show_thr = bool(getattr(getattr(ctx.state, "plot", None),
                             "show_threshold_line", False))
    if (mode != "Violin (per group)" and _show_thr
            and not ctx.state.channel.is_shape_active()):
        try:
            threshold = get_thresh_frac_on(ctx, ctx.state.channel.active_channel)
            if math.isfinite(threshold):
                ax.axvline(threshold, color="orange", linestyle="--", linewidth=1.0, alpha=0.8)
        except Exception:
            swallowed()  # was: pass (silent except)

    title = _title_for(ctx, tp_h, int(sum(int(vs.size) for _, _, vs in groups)), len(groups))
    ax.set_title(title, fontsize=9)


def _render_faceted(ctx, groups, mode, bins, log_x, tp_h, *, log_y=False) -> None:
    """One axis per group, stacked vertically with minimal padding.

    Covers the histogram modes (Histogram / Histogram + KDE) and CDF — every
    mode whose curves benefit from a per-group panel. Shared x-axis
    (``sharex=True`` via ``_setup_axes``) + shared bin edges means every panel
    reads on the same grid; the user can scan vertically to compare
    distributions without per-axis re-binning differences swamping the
    comparison.
    """
    axes = _setup_axes(ctx, len(groups))
    is_cdf = mode == "CDF"
    # Bin edges / KDE grid are only meaningful for the histogram modes; skip the
    # pooling work entirely for CDF (which needs neither).
    if not is_cdf:
        edges = _shared_bin_edges((vs for _, _, vs in groups), bins, log_x)
        grid = _grid_for((vs for _, _, vs in groups), log_x=log_x)

    # Shapes have no ON cutoff → no threshold line. smFISH counts now draw
    # the ThreshFracOn line like intensities.
    if ctx.state.channel.is_shape_active():
        threshold = float("nan")
    else:
        try:
            threshold = get_thresh_frac_on(ctx, ctx.state.channel.active_channel)
        except Exception:
            threshold = float("nan")
    show_threshold = math.isfinite(threshold) and bool(getattr(
        getattr(ctx.state, "plot", None), "show_threshold_line", False))

    last_idx = len(groups) - 1
    for i, ((name, color, vals), ax) in enumerate(zip(groups, axes)):
        ax.set_axis_on()
        if is_cdf:
            # Empirical CDF per panel — mirrors the overlay CDF branch, one
            # monotone step curve per group so stacked comparison is legible.
            arr = np.asarray(vals, dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size:
                xs = np.sort(arr)
                ys = np.arange(1, arr.size + 1, dtype=float) / float(arr.size)
                ax.step(xs, ys, where="post", color=color, lw=1.6)
        else:
            if mode in ("Histogram", "Histogram + KDE"):
                # ``log=log_y`` — see the overlay renderer note: drawing density
                # bars from a 0 baseline then setting log-Y afterward renders
                # wrong heights.
                ax.hist(
                    vals, bins=edges, density=True, alpha=0.55, log=log_y,
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
        elif is_cdf:
            # Fixed 0..1 window (with a hair of padding) so every CDF panel
            # shares the same cumulative-fraction axis; the linear overlay CDF
            # uses the same limits.
            ax.set_ylim(-0.02, 1.05)
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
    axes[last_idx].set_xlabel(_xlabel_for(ctx))
    # Single shared y-label across the middle axis. "Density" for the histogram
    # modes (each hist uses density=True); "Cumulative fraction" for CDF.
    axes[len(axes) // 2].set_ylabel(
        "Cumulative fraction" if is_cdf else "Density")
    # Use a figure-level suptitle so the title isn't duplicated per axis.
    fig = ctx.widgets.get("_distribution_fig")
    title = _title_for(
        ctx, tp_h, int(sum(int(vs.size) for _, _, vs in groups)), len(groups),
    )
    fig.suptitle(title, fontsize=9)


def _xlabel_for(ctx) -> str:
    label = active_channel_label(ctx)
    metric = ctx.state.channel.active_metric
    if metric and not str(ctx.state.channel.active_val_col).startswith("ratio:"):
        return f"{label} {metric.replace('_', ' ')}"
    return label


def _title_for(ctx, tp_h: float, n_cells: int, n_groups: int) -> str:
    label = active_channel_label(ctx)
    if math.isfinite(tp_h):
        tp_str = f"t = {tp_h:g} h"
    else:
        tp_str = "all timepoints"
    return f"{label} distribution — {tp_str} ({n_cells} cells, {n_groups} groups)"
