"""Bar-plot group state serialization helpers extracted from well_viewer3."""

from __future__ import annotations

from well_viewer.features.barplot._axis_fit import fit_bar_panels
from well_viewer.domain.numeric import is_number
from well_viewer.domain.selections_model import well_display_label, active_replicate_sets
from well_viewer.domain.viewer_state import parse_rc
from widgets import menu_exec

import logging
import math
import re

from well_viewer.domain.batch_models import ReplicateSet
from well_viewer.domain.fold_change_pairing import (
    tick_labels as _tick_labels,
)
from well_viewer.core import debug_flags as debug_flags
from well_viewer.core.ui_helpers import set_combo_values as _set_combo_values
from well_viewer.core.ui_helpers import multiselect_button_text
from well_viewer.data.data_loading import extract_well_token as _extract_well_token

_logger = logging.getLogger(__name__)






# ``bar_groups_to_dict`` / ``bar_groups_from_data`` are pure domain transforms;
# they moved to ``well_viewer.domain.bar_groups`` so the domain layer can use
# them without importing this feature. Re-exported here for existing callers.
from well_viewer.domain.bar_groups import (  # noqa: F401
    bar_groups_from_data,
    bar_groups_to_dict,
)

# The pure data-collection cluster moved to ``barplot/collect.py`` (T4). The
# renderer/state functions below still call ``collect_bar_items`` / ``BarItem``,
# and external callers (runtime_app, batch_export, export.service, tests) import
# these from here — so re-export them.
from well_viewer.features.barplot.collect import (  # noqa: F401
    BarItem,
    FC_STATE_OFF,
    _bar_debug,
    bar_drag_xs,
    bar_err_settings,
    bar_guide_x,
    bar_idx_at_x,
    cluster_x_for_group_keys,
    clustered_x_positions,
    collect_bar_items,
    collect_bar_items_for_app,
    collect_bar_items_for_group,
    collect_bar_items_fov_pooled,
    drop_redundant_cluster_keys,
)


def ordered_bar_keys(ctx) -> list:
    """Return ordered bar keys for current mode, respecting custom drag order."""
    active_rsets = active_replicate_sets(ctx.state.selection.selections, ctx.state.dataset.well_paths)
    if active_rsets:
        keys = [r.name for r in active_rsets]
    elif ctx.state.selection.selections:
        # Group mode (replicate sets / groups defined) but every group hidden →
        # no bars. Don't fall back to ``selected_wells`` here: in group mode the
        # plate is passive and clicks toggle group visibility, so selected_wells
        # is stale — falling back resurrected every well after "Deselect all".
        keys = []
    else:
        keys = sorted(
            (lbl for lbl in ctx.state.selection.selected_wells if lbl in ctx.state.dataset.well_paths),
            key=lambda lbl: parse_rc(lbl),
        )
    if ctx.state.plot.bar_order is None:
        return keys
    ordered = [k for k in ctx.state.plot.bar_order if k in keys]
    seen = set(ordered)
    ordered.extend(k for k in keys if k not in seen)
    return ordered


# ``cluster_x_for_group_keys`` / ``clustered_x_positions`` moved to
# ``barplot/collect.py`` (Qt-free layout math) and re-exported above.


def cluster_centers(items: list[BarItem], xs: list[float]):
    """Yield ``(gk, cx)`` for each non-empty cluster in encounter order.
    Public so the renderer can re-use the same iteration for footers."""
    cluster_x: dict = {}
    cluster_order: list[str] = []
    for it, x in zip(items, xs):
        gk = it.group_key or ""
        if not gk:
            continue
        if gk not in cluster_x:
            cluster_x[gk] = []
            cluster_order.append(gk)
        cluster_x[gk].append(x)
    for gk in cluster_order:
        positions = cluster_x[gk]
        if positions:
            yield gk, sum(positions) / len(positions)


def _annotate_cluster_headers(items: list[BarItem], xs: list[float], ax) -> None:
    """Per-cluster label centred above the mean panel."""
    if not items or not xs:
        return
    from well_viewer.core.plot_style import tokens_for
    _fg = tokens_for(ax)[1]
    for gk, cx in cluster_centers(items, xs):
        ax.text(cx, 1.02, gk, transform=ax.get_xaxis_transform(),
                ha="center", va="bottom", fontsize=8, fontweight="bold",
                color=_fg)


def _mark_undefined_spread(ax, x: float, y: float, color: str) -> None:
    """Mark a bar whose spread could not be computed, when error bars are on.

    A hollow cap and an ``n/a`` above the bar, in the error-bar colour. The
    point is only to make "not computed" visually distinct from "computed and
    small" — a bar that silently loses its whisker is indistinguishable from one
    whose error bars were turned off, and that ambiguity is how a well with
    pooled (unusable) FOV labels passed for a well with tight replicates.
    """
    import math as _math

    if y is None or not _math.isfinite(float(y)):
        return
    ax.plot([x], [y], marker="_", markersize=9, markeredgewidth=1.4,
            color=color, alpha=0.7, linestyle="none", zorder=4)
    ax.annotate("n/a", (x, y), textcoords="offset points", xytext=(0, 7),
                ha="center", va="bottom", fontsize=7, color=color, alpha=0.85,
                zorder=5, annotation_clip=False)


def render_bar_items(
    *,
    ax_mean,
    ax_frac,
    use_groups: bool,
    items: list[BarItem],
    threshold: float,
    warn_color: str,
    border_color: str,
    placeholder_color: str,
    disabled_well_color: str,
    err_bar_color: str,
    ax_n=None,
    fc_active: bool = False,
    n_axis_integer: bool | None = None,
    ctx=None,
) -> None:
    """Draw mean / fraction / events-above-threshold bar panels.

    Consumes ``BarItem`` records emitted by :func:`collect_bar_items`. All
    per-bar state (display label, colour, value, spread, has-flags) lives on
    the item, so the renderer doesn't second-guess what to draw. ``use_groups``
    only governs the bar-width preset — grouped mode draws slightly wider
    bars to differentiate visually from the per-well view.
    """
    n = len(items)
    # Drop cluster headers that would only duplicate a single bar's own tick
    # label (e.g. one pooled group per a single timepoint) before laying out —
    # otherwise a redundant bold label renders atop the Mean panel, overlapping
    # the figure title.
    drop_redundant_cluster_keys(items)
    xlabels = _tick_labels(items)
    keys = [item.key for item in items]
    _bar_debug(
        f"render_bar_items use_groups={use_groups} n={n} keys={keys!r} xlabels={xlabels!r}"
    )

    xs = clustered_x_positions(items)
    # Stash the real positions (aligned to the key order) so the drag-reorder
    # hit-test maps clicks to the right column when clusters insert gaps.
    if ctx is not None:
        ctx.runtime.barplot.xs = list(xs)
        ctx.runtime.barplot.xs_keys = list(keys)
    # Error-bar appearance from the Properties → Statistics controls.
    err_kind, ci_scale, draw_err = bar_err_settings(ctx)
    # Opt-in diagnostic: with review_bar_debug on, dump per-item spreads so a
    # single desktop retest tells us whether the Fraction/Events panels are
    # blank because their spreads arrive zero (legitimate / data) vs a draw-gate
    # bug. ``draw_err`` is shared across all three panels — if Mean draws error
    # bars but the lower two don't, the lower spreads are zero here.
    _bar_debug(
        f"render_bar_items draw_err={draw_err} err_kind={err_kind} "
        f"ci_scale={ci_scale} spreads="
        + repr([(it.key, round(it.spread, 4), round(it.frac_spread, 4),
                 round(it.n_above_spread, 4)) for it in items])
    )
    has_clusters = bool(xs and any(_ != i for i, _ in enumerate(xs)))
    bar_w = min(0.65 if use_groups else 0.6, 5.0 / max(n, 1))
    for i, item in enumerate(items):
        x = xs[i] if i < len(xs) else i
        color = item.color
        # Mean panel.
        if item.has_mean and not math.isnan(item.mean):
            ax_mean.bar(x, item.mean, width=bar_w, color=color, alpha=0.85,
                        zorder=3, linewidth=0)
            if draw_err and item.spread > 0:
                ax_mean.errorbar(x, item.mean, yerr=item.spread * ci_scale,
                                 fmt="none", ecolor=err_bar_color, elinewidth=1.4,
                                 capsize=4, zorder=4)
            elif draw_err and math.isnan(item.spread):
                # The spread was never computed (one replicate, or FOV labels
                # that don't identify the fields). Drawing nothing here is what
                # made a bar with no computable error look exactly like a bar
                # whose error bars were switched off — the reader has no way to
                # tell "n=1" from "the replicates agreed".
                _mark_undefined_spread(ax_mean, x, item.mean, err_bar_color)
        else:
            ax_mean.bar(x, 0, width=bar_w, color=placeholder_color,
                        linewidth=1, edgecolor=disabled_well_color,
                        linestyle="--", zorder=3)
        # Fraction panel.
        if item.has_frac and not math.isnan(item.frac):
            ax_frac.bar(x, item.frac, width=bar_w, color=color, alpha=0.85,
                        zorder=3, linewidth=0)
            if draw_err and item.frac_spread > 0:
                ax_frac.errorbar(x, item.frac, yerr=item.frac_spread * ci_scale,
                                 fmt="none", ecolor=err_bar_color,
                                 elinewidth=1.4, capsize=4, zorder=4)
            elif draw_err and math.isnan(item.frac_spread):
                _mark_undefined_spread(ax_frac, x, item.frac, err_bar_color)
        else:
            ax_frac.bar(x, 0, width=bar_w, color=placeholder_color,
                        linewidth=1, edgecolor=disabled_well_color,
                        linestyle="--", zorder=3)
        # Events-above-threshold panel (optional).
        if ax_n is not None:
            # ``n_above > 0`` conflated two different facts: a set that
            # genuinely counted zero events above threshold, and one whose
            # count could not be computed. Both drew the dashed placeholder.
            # Zero is a measurement — it gets a real (zero-height) bar, and the
            # placeholder is kept for the undefined case.
            #
            # **Not gated on ``has_mean``**, which was the same conflation one
            # step removed: ``_aggregate_arrays`` sets ``mean = nan`` exactly
            # when nothing passed the threshold, so requiring a mean here threw
            # the placeholder over the commonest measured zero there is. The
            # count answers its own question — the mean is the average intensity
            # *of the cells above threshold*, undefined when there are none,
            # while the count of those cells is 0 and known. This is the same
            # separation ``has_frac`` needed in the collector.
            _n_above_known = is_number(item.n_above)
            if _n_above_known:
                ax_n.bar(x, item.n_above, width=bar_w, color=color,
                         alpha=0.85, zorder=3, linewidth=0)
                if draw_err and item.n_above_spread > 0:
                    ax_n.errorbar(x, item.n_above, yerr=item.n_above_spread * ci_scale,
                                  fmt="none", ecolor=err_bar_color,
                                  elinewidth=1.4, capsize=4, zorder=4)
                elif draw_err and math.isnan(item.n_above_spread):
                    _mark_undefined_spread(ax_n, x, item.n_above, err_bar_color)
            else:
                ax_n.bar(x, 0, width=bar_w, color=placeholder_color,
                         linewidth=1, edgecolor=disabled_well_color,
                         linestyle="--", zorder=3)

    if ctx is not None and getattr(ctx.state.plot, "show_threshold_line", False):
        ax_frac.axhline(0.5, color=border_color, lw=0.8, ls="--", alpha=0.5, zorder=1)
        # Add threshold context label to fraction axis
        ax_frac.text(0.02, 0.98, f"threshold={threshold:.2f}", transform=ax_frac.transAxes,
                     fontsize=8, va="top", ha="left", color=warn_color, alpha=0.7)

    axes_to_label = [ax_mean, ax_frac]
    if ax_n is not None:
        axes_to_label.append(ax_n)
    rot = 45 if n > 8 else 0
    ha = "right" if n > 8 else "center"
    xlim_lo = (min(xs) - 0.6) if xs else -0.6
    xlim_hi = (max(xs) + 0.6) if xs else 0.4
    for ax in axes_to_label:
        ax.set_xticks(xs)
        ax.set_xticklabels(xlabels, rotation=rot, ha=ha, fontsize=7)
        ax.set_xlim(xlim_lo, xlim_hi)
        # Mark categorical x-axis so downstream styling does not reset tick formatters.
        setattr(ax, "_categorical_xaxis", True)
    if has_clusters:
        _annotate_cluster_headers(items, xs, ax_mean)

    # ── Auto-fit y-limits ────────────────────────────────────────────────────
    # Honest 0 baseline; trim top to tallest bar + error + ~8% headroom so the
    # error caps aren't clipped. User-entered y-limits (Properties sidebar) are
    # applied last, per-panel, by ``apply_export_style_to_current`` at the end
    # of ``redraw_bars``. The arithmetic and the events-axis tick rule live in
    # ``_axis_fit``; both are decisions this codebase has got wrong by copying a
    # sibling's rule without checking it applied, and the reasoning is written
    # out there.
    fit_bar_panels(
        ax_mean, ax_frac, ax_n, items,
        fc_active=fc_active,
        n_axis_integer=n_axis_integer,
        per_fov=bool(
            getattr(getattr(ctx, "state", None), "plot", None)
            and ctx.state.plot.use_fov_replicates
        ),
    )

# ── Drag-and-drop reordering ─────────────────────────────────────────────────
#
# Lightweight helpers that drive the bar drag interaction. The view (matplotlib
# canvas + axes) is owned by ``WellViewerApp``; these helpers only mutate
# ``ctx.runtime.barplot.drag_state`` / ``ctx.state.plot.bar_order`` and trigger a redraw.

def bar_event_xdata(ctx, event) -> float | None:
    """Return data-x for a matplotlib MouseEvent over any bar axis."""
    ax = getattr(event, "inaxes", None)
    if ax is not ctx.widgets.get_or_none("_ax_bar_mean") and ax is not ctx.widgets.get_or_none("_ax_bar_frac") and ax is not ctx.widgets.get_or_none("_ax_bar_n"):
        return None
    xdata = getattr(event, "xdata", None)
    if xdata is None:
        return None
    return float(xdata)


# ``bar_idx_at_x`` / ``bar_drag_xs`` / ``bar_guide_x`` / ``bar_err_settings``
# moved to ``barplot/collect.py`` (Qt-free) and re-exported above.


def bar_reset_order(ctx) -> None:
    ctx.state.plot.bar_order = None
    ctx.runtime.barplot.reset_order_btn.setProperty("variant", "toggle_muted")
    ctx.runtime.barplot.reset_order_btn.style().unpolish(ctx.runtime.barplot.reset_order_btn)
    ctx.runtime.barplot.reset_order_btn.style().polish(ctx.runtime.barplot.reset_order_btn)
    from well_viewer.features.barplot.renderer import redraw_bars
    redraw_bars(ctx)


def on_bar_drag_press(ctx, event) -> None:
    """Begin drag — record which bar was pressed."""
    if getattr(event, "button", None) != 1:
        return
    xdata = bar_event_xdata(ctx, event)
    if xdata is None:
        return
    keys = ctx.runtime.barplot.current_keys()
    n = len(keys)
    if n < 2:
        return
    idx = bar_idx_at_x(xdata, n, bar_drag_xs(ctx, keys))
    ctx.runtime.barplot.drag_state.update(active=True, src_idx=idx, cur_idx=idx)


def on_bar_drag_motion(ctx, event, *, accent_color: str) -> None:
    """Update drop-target indicator while dragging."""
    ds = ctx.runtime.barplot.drag_state
    if not ds["active"]:
        return
    xdata = bar_event_xdata(ctx, event)
    if xdata is None:
        return
    keys = ctx.runtime.barplot.current_keys()
    n = len(keys)
    if n < 2:
        return
    xs = bar_drag_xs(ctx, keys)
    tgt = bar_idx_at_x(xdata, n, xs)
    if tgt == ds["cur_idx"]:
        return
    ds["cur_idx"] = tgt

    guide_x = bar_guide_x(xs, tgt, ds["src_idx"], n)
    for ax in (ctx.widgets.get_or_none("_ax_bar_mean"), ctx.widgets.get_or_none("_ax_bar_frac"), ctx.widgets.get_or_none("_ax_bar_n")):
        if ax is None:
            continue
        for ln in list(ax.lines):
            if getattr(ln, "_bar_drag_guide", False):
                ln.remove()
        ln = ax.axvline(guide_x, color=accent_color, lw=1.5, ls="--",
                        alpha=0.8, zorder=10)
        ln._bar_drag_guide = True
    ctx.runtime.barplot.canvas.draw_idle()


def on_bar_drag_release(ctx, event) -> None:
    """Finalise drop — reorder and redraw."""
    ds = ctx.runtime.barplot.drag_state
    if not ds["active"]:
        return
    ds["active"] = False

    for ax in (ctx.widgets.get_or_none("_ax_bar_mean"), ctx.widgets.get_or_none("_ax_bar_frac"), ctx.widgets.get_or_none("_ax_bar_n")):
        if ax is None:
            continue
        for ln in list(ax.lines):
            if getattr(ln, "_bar_drag_guide", False):
                ln.remove()

    src = ds["src_idx"]
    tgt = ds["cur_idx"]
    if src == tgt:
        ctx.runtime.barplot.canvas.draw_idle()
        return

    keys = ctx.runtime.barplot.current_keys()
    if not (0 <= src < len(keys) and 0 <= tgt < len(keys)):
        ctx.runtime.barplot.canvas.draw_idle()
        return

    item = keys.pop(src)
    keys.insert(tgt, item)
    ctx.state.plot.bar_order = keys
    ctx.runtime.barplot.reset_order_btn.setProperty("variant", "toggle_accent")
    ctx.runtime.barplot.reset_order_btn.style().unpolish(ctx.runtime.barplot.reset_order_btn)
    ctx.runtime.barplot.reset_order_btn.style().polish(ctx.runtime.barplot.reset_order_btn)
    from well_viewer.features.barplot.renderer import redraw_bars
    redraw_bars(ctx)


def update_bar_tp_menu(ctx) -> None:
    """
    Populate the timepoint menu with the union of all timepoints
    present across all loaded wells.

    Maintains two pieces of state in sync:
      * ``ctx.state.bar.tp_selections`` — the multi-select dict
        backing the popup checkbox menu (BarPlotState).
      * ``ctx.runtime.barplot.tp_cb`` — a hidden combo holding the first checked tp
        string so legacy callers (export, view-state, plot orchestrator)
        that read ``currentText()`` continue to work.
    """
    bar_state = ctx.state.bar

    if not ctx.state.dataset.well_paths:
        bar_state.tp_selections = {}
        if ctx.runtime.barplot.tp_cb is not None:
            _set_combo_values(ctx.runtime.barplot.tp_cb, ["—"])
            ctx.runtime.barplot.tp_cb.setCurrentText("—")
        update_bar_tp_selection_display(ctx)
        return

    all_tps: set = set(ctx.state.dataset.all_timepoints_cache)
    # If no timepoints were found the schema had no timepoint field.
    # Use 0.0 as a single synthetic timepoint so bar plots remain usable.
    if not all_tps:
        all_tps.add(0.0)

    sorted_tps = sorted(all_tps)
    tp_strs = [f"{t:.4g}" for t in sorted_tps]

    cur = ctx.runtime.barplot.tp_cb.currentText() if (ctx.runtime.barplot.tp_cb is not None) else ""

    # Preserve any existing selections; default-check the previously
    # selected single tp on first build, falling back to the first tp.
    new_selections: dict[str, bool] = {}
    existing = bar_state.tp_selections
    any_existing_on = any(existing.values()) if existing else False
    for s in tp_strs:
        if existing and s in existing:
            new_selections[s] = bool(existing[s])
        else:
            new_selections[s] = False
    if not any_existing_on:
        if cur in new_selections:
            new_selections[cur] = True
        elif tp_strs:
            new_selections[tp_strs[0]] = True
    bar_state.tp_selections = new_selections

    if ctx.runtime.barplot.tp_cb is not None:
        _set_combo_values(ctx.runtime.barplot.tp_cb, tp_strs)
        first_on = next((s for s in tp_strs if new_selections.get(s)), None)
        if first_on is not None:
            ctx.runtime.barplot.tp_cb.setCurrentText(first_on)
        elif tp_strs:
            ctx.runtime.barplot.tp_cb.setCurrentText(tp_strs[0])
        else:
            ctx.runtime.barplot.tp_cb.setCurrentText("—")
    update_bar_tp_selection_display(ctx)






def toggle_swarm(ctx) -> None:
    """Toggle beeswarm / bar mode and update the button appearance."""
    from well_viewer.features.barplot.renderer import redraw_bars
    bs = ctx.state.bar
    bs.swarm = not bs.swarm
    on = bs.swarm
    ctx.widgets.get_or_none("_swarm_btn").setChecked(on)
    ctx.widgets.get_or_none("_swarm_btn").setText("Beeswarm: On" if on else "Beeswarm: Off")
    if on and bs.violin:
        # Swarm and violin are mutually exclusive
        bs.violin = False
        ctx.widgets.get_or_none("_violin_btn").setChecked(False)
        ctx.widgets.get_or_none("_violin_btn").setText("Violin: Off")
        ctx.widgets.get_or_none("_violin_slider").setEnabled(False)
    refresh_bar_layout_btn_state(ctx)
    redraw_bars(ctx)



def toggle_violin(ctx) -> None:
    """Toggle violin / bar mode and update the button appearance."""
    from well_viewer.features.barplot.renderer import redraw_bars
    bs = ctx.state.bar
    bs.violin = not bs.violin
    on = bs.violin
    ctx.widgets.get_or_none("_violin_btn").setChecked(on)
    ctx.widgets.get_or_none("_violin_btn").setText("Violin: On" if on else "Violin: Off")
    ctx.widgets.get_or_none("_violin_slider").setEnabled(on)
    if on and bs.swarm:
        # Mutually exclusive with beeswarm
        bs.swarm = False
        ctx.widgets.get_or_none("_swarm_btn").setChecked(False)
        ctx.widgets.get_or_none("_swarm_btn").setText("Beeswarm: Off")
    refresh_bar_layout_btn_state(ctx)
    redraw_bars(ctx)



def rep_sets_active(ctx) -> list[ReplicateSet]:
    """One ReplicateSet per visible (non-hidden) selection with ≥1 loaded
    well — these are exactly the traces drawn on the plots.

    A8 teardown: takes ``ctx`` — delegates to the pure domain enumerator over
    ``ctx.state`` (no god-object)."""
    from well_viewer.domain.selections_model import active_replicate_sets
    return active_replicate_sets(
        ctx.state.selection.selections, ctx.state.dataset.well_paths)



def bar_well_display_label(ctx, lbl: str) -> str:
    """Bar-plot-safe well label that avoids numeric-only tick text."""
    disp = str(well_display_label(ctx, lbl))
    if re.match(r"^\s*\d+\s*$", disp):
        return _extract_well_token(lbl) or disp
    return disp



# ``toggle_fov_replicates`` moved to ``well_viewer.core.plot_toggles`` (A8
# enabler E3): it drives an app-global control shared by the plot tabs and the
# batch-export inline row, so it lives in core where any feature can call it
# through ctx. Its bar-renderer / bar-layout wiring is reached there via
# core.lazy proxies to keep core feature-import-free.


def toggle_bar_group_mode(ctx) -> None:
    """Flip the bar-tab x-axis grouping (well ↔ timepoint) and redraw."""
    from well_viewer.features.barplot.renderer import redraw_bars
    ctx.state.plot.toggle_bar_group_mode()
    refresh_bar_layout_btn_state(ctx)
    redraw_bars(ctx)


def toggle_bar_pool_replicates(ctx) -> None:
    """Flip whether replicate-set wells collapse to a single column."""
    from well_viewer.features.barplot.renderer import redraw_bars
    ctx.state.plot.toggle_bar_pool_replicates()
    refresh_bar_layout_btn_state(ctx)
    redraw_bars(ctx)


def update_bar_tp_selection_display(ctx) -> None:
    """Refresh the "Timepoints: …" readout painted on the picker button itself."""
    btn = getattr(ctx.runtime.barplot, "tp_button", None)
    if btn is None:
        return
    btn.setText(multiselect_button_text("Timepoints", ctx.state.bar.tp_selections))


def sync_hidden_bar_tp_combo(ctx) -> None:
    """Mirror the first checked tp into the hidden ``_bar_tp_cb`` combo.

    Legacy callers (export/service.py, persistence/view_state.py,
    batch_export/bar_panel.py, core/plot_orchestrator.py) still read
    ``_bar_tp_cb.currentText()``. Keep the combo's items as the full
    sorted tp list (set by ``update_bar_tp_menu``) and snap its current
    text to the first checked tp here.
    """
    sels = ctx.state.bar.tp_selections
    if not (ctx.runtime.barplot.tp_cb is not None):
        return
    if not sels:
        return
    for tp_str in sorted(sels.keys(), key=float):
        if sels[tp_str]:
            ctx.runtime.barplot.tp_cb.setCurrentText(tp_str)
            return


def open_bar_timepoint_selector(ctx) -> None:
    """Pop up a QMenu of checkboxes below the bar tab's Timepoints button.

    Mirrors ``features/scatter/cells_tab_view.py:_open_timepoint_selector``
    so the bar plot can show bars / violins / beeswarms over multiple
    timepoints simultaneously. Lives in the controller rather than the
    tab builder so all bar-tab behaviour stays in one module (§5.5).
    """
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QCheckBox, QMenu, QWidgetAction
    from well_viewer.features.barplot.renderer import redraw_bars

    menu = QMenu(ctx.runtime.barplot.tp_button)

    sels = ctx.state.bar.tp_selections
    checkboxes: dict[str, QCheckBox] = {}
    for tp_str in sorted(sels.keys(), key=float):
        cb = QCheckBox(tp_str, menu)
        cb.setChecked(bool(sels[tp_str]))
        checkboxes[tp_str] = cb

        def _make_handler(tp=tp_str, checkbox=cb):
            def on_toggled(checked: bool) -> None:
                ctx.state.bar.tp_selections[tp] = bool(checked)
                sync_hidden_bar_tp_combo(ctx)
                update_bar_tp_selection_display(ctx)
                redraw_bars(ctx)
            return on_toggled

        cb.toggled.connect(_make_handler())
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(cb)
        menu.addAction(wa)

    menu.addSeparator()

    def _set_all(value: bool) -> None:
        for tp_str, cb in checkboxes.items():
            cb.blockSignals(True)
            try:
                cb.setChecked(value)
            finally:
                cb.blockSignals(False)
            ctx.state.bar.tp_selections[tp_str] = value
        sync_hidden_bar_tp_combo(ctx)
        update_bar_tp_selection_display(ctx)
        redraw_bars(ctx)

    all_action = QAction("All", menu)
    all_action.triggered.connect(lambda _=False: _set_all(True))
    menu.addAction(all_action)
    none_action = QAction("None", menu)
    none_action.triggered.connect(lambda _=False: _set_all(False))
    menu.addAction(none_action)

    btn = ctx.runtime.barplot.tp_button
    menu_exec.exec_menu(menu, btn.mapToGlobal(btn.rect().bottomLeft()))


def refresh_bar_layout_btn_state(ctx) -> None:
    """Sync the bar-tab layout toggle buttons with PlotState.

    Updates the *Grouping* and *Pooled* button labels / checked states using the
    "State: On/Off" convention (label and accent highlight always agree). The
    *Pooled* button stays visible in the Display ▾ popover and is enable-gated
    instead of visibility-toggled (toggling a child's visibility inside a closed
    QMenu widget-action does not surface reliably); it is enabled only when ≥1
    replicate set is active. It governs pooling for bars *and* violin/beeswarm:
    on collapses each set into one column (its cells pooled for the per-cell
    views), off keeps one column per well.
    """
    plot = ctx.state.plot
    group_btn = getattr(ctx.runtime.barplot, "group_mode_btn", None)
    pool_btn = getattr(ctx.runtime.barplot, "pool_reps_btn", None)
    has_rsets = bool(active_replicate_sets(ctx.state.selection.selections, ctx.state.dataset.well_paths))
    if group_btn is not None:
        # Two-way mode selector: label names the current mode, highlight tracks
        # the non-default "timepoint" mode so label and highlight never conflict.
        if plot.bar_group_mode == "well":
            group_btn.setText("Grouping: Well")
        else:
            group_btn.setText("Grouping: Timepoint")
        group_btn.setChecked(plot.bar_group_mode == "timepoint")
        group_btn.setEnabled(True)
    if pool_btn is not None:
        pool_btn.setVisible(True)
        pool_btn.setEnabled(has_rsets)
        # State-label + matching highlight: highlighted iff pooling is on.
        pool_btn.setText("Pooled: On" if plot.bar_pool_replicates else "Pooled: Off")
        pool_btn.setChecked(bool(plot.bar_pool_replicates))
        pool_btn.setToolTip(
            "Collapse each replicate set's wells into one pooled column "
            "(off = one column per well)." if has_rsets
            else "Define replicate groups (Home → Groups) to "
            "pool them.")

