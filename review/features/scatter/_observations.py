"""Scatter under **Observations: Nanowells** — the axis pickers and the redraw (ARCHITECTURE §6.2l).

The Cells and Wells pages each get an X / Y metric picker for nanowell observations beside their per-cell channel
and property pickers; :func:`sync_controls` shows one set or the other, so the band only ever carries controls
that drive the plot on screen. The points come from ``observation_compute.nanowell_scatter``; the Wells page draws
each well's median over its nanowells.

The nanowell metric names are the Nanowells feature's, read from the table it hands back — this module never
imports that feature (``review/data/observations``).
"""

from __future__ import annotations

from widgets.plot_card import follows_theme

from review.domain.observation import NANOWELLS
from review.features.scatter.collect import scatter_groups
from review.features.scatter.observation_compute import click_points, default_axes, nanowell_scatter

#: The leaf this module serves (its registry name).
LEAF = "Scatter Plot"
#: Widget-registry names of the two pages' cell-axis combos, hidden under Nanowells.
_CELL_AXIS = {
    "cells": ("_scatter_ch_x_cb", "_scatter_metric_x_cb", "_scatter_ch_y_cb", "_scatter_metric_y_cb"),
    "aggregate": ("_scatter_agg_ch_x_cb", "_scatter_agg_metric_x_cb", "_scatter_agg_ch_y_cb",
                  "_scatter_agg_metric_y_cb"),
}
#: The page's figure handles: (axes, figure, canvas).
_FIGURE = {"cells": ("_ax_scatter", "_scatter_fig", "_scatter_canvas"),
           "aggregate": ("_ax_scatter_agg", "_scatter_agg_fig", "_scatter_agg_canvas")}
#: The page's runtime timepoint selection attribute.
_TP_SELECTIONS = {"cells": "tp_selections", "aggregate": "agg_tp_selections"}


def _combo_names(page: str) -> tuple[str, str]:
    return f"_scatter_{page}_nw_x_cb", f"_scatter_{page}_nw_y_cb"


def _scope_names(page: str) -> tuple[str, str]:
    """The page's Subset and Baseline pickers: the Nanowells page's own two settings, offered here."""
    return f"_scatter_{page}_nw_subset_cb", f"_scatter_{page}_nw_baseline_cb"


def build_axis_controls(ctx, slot, page: str) -> None:
    """Add the page's nanowell X / Y metric pickers, and its Subset and Baseline pickers, to its band *slot*, hidden;
    :func:`sync_controls` shows them.

    **Subset** and **Baseline** are the Nanowells page's settings, not copies of them: choosing one here sets the
    page's (``nanowell.set_subset_by_name`` / ``nanowell.set_baseline``), so the page's views and this scatter stay
    about the same nanowells and the same fold changes, whichever of the two the choice was made on."""
    from widgets.labeled_combo import LabeledComboBox

    for name, caption in zip(_combo_names(page), ("X", "Y")):
        combo = ctx.widgets.register(name, LabeledComboBox(caption))
        combo.setFitsWidest(True)                        # one width whatever metric is chosen
        combo.currentIndexChanged.connect(lambda _i, _p=page: _redraw_page(ctx, _p))
        group = slot.add_scope("", combo)
        group.setVisible(False)
        ctx.widgets.register(name + "_grp", group)
    tips = ("Which nanowells are drawn: the Nanowells page's Subset. Choosing one here makes it that page's Subset.",
            "What every fold change is measured from: the Nanowells page's Baseline. Choosing one here sets it there.")
    for name, caption, tip in zip(_scope_names(page), ("Subset", "Baseline"), tips):
        combo = ctx.widgets.register(name, LabeledComboBox(caption))
        combo.setToolTip(tip)
        combo.currentIndexChanged.connect(
            lambda _i, _c=combo, _k=caption, _p=page: _choose(ctx, _k, _c.currentData(), _p))
        group = slot.add_scope("", combo)
        group.setVisible(False)
        ctx.widgets.register(name + "_grp", group)


def _choose(ctx, setting: str, value, page: str) -> None:
    """Set the Nanowells page's *setting* (``"Subset"`` or ``"Baseline"``) to *value*, then redraw *page* from the
    page's new scope."""
    if value is None:
        return
    ctx.features.query("nanowell.set_subset_by_name" if setting == "Subset" else "nanowell.set_baseline",
                       ctx, value, default=None)
    _redraw_page(ctx, page)


#: The gid of the line naming the Nanowells page's Subset, removed with the footer before each draw.
SUBSET_LABEL_GID = "nw-subset-label"


def _stamp_subset(ctx, fig, muted) -> None:
    """Write which of the Nanowells page's subsets the scatter draws at its top left — the saved name, else the
    conditions (``nanowell.subset_label``) — as the page's own figures do, so a saved or exported scatter says it is
    of a subset. Nothing with no Subset, or in a build without the Nanowells page."""
    text = ctx.features.query("nanowell.subset_label", ctx, default="") or ""
    if text:
        follows_theme(fig.text(0.01, 0.995, text, ha="left", va="top", fontsize=8, color=muted,
                               gid=SUBSET_LABEL_GID), muted=True)


def _populate_scope(ctx, page: str, table) -> None:
    """Fill the page's Subset and Baseline pickers with the Nanowells page's choices and select the page's own; hide
    them in a build without that page. The Baseline lists the timepoints of *table*'s rows in time order."""
    order = (table.frame.drop_duplicates("timepoint").sort_values("timepoint_hours")["timepoint"].tolist()
             if "timepoint_hours" in table.frame.columns and len(table.frame) else [])
    choices = ctx.features.query("nanowell.scope_choices", ctx, [t for t in order if t != ""], default=None)
    for name, items, current in zip(_scope_names(page), ("subsets", "baselines"), ("subset", "baseline")):
        combo = ctx.widgets.get_or_none(name)
        group = ctx.widgets.get_or_none(name + "_grp")
        if group is not None:
            group.setVisible(choices is not None)
        if combo is None or choices is None:
            continue
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            for key, text in choices[items]:
                combo.addItem(text, key)
            keys = [key for key, _text in choices[items]]
            if choices[current] in keys:
                combo.setCurrentIndex(keys.index(choices[current]))
        finally:
            combo.blockSignals(blocked)


def _redraw_page(ctx, page: str) -> None:
    from review.core.observation_band import effective_observations

    if effective_observations(ctx, LEAF) == NANOWELLS:
        redraw(ctx, page)


def sync_controls(ctx) -> str:
    """Show the nanowell pickers or the cell pickers on both pages; returns the observations in force."""
    from review.core.observation_band import effective_observations

    nanowells = effective_observations(ctx, LEAF) == NANOWELLS
    for page, names in _CELL_AXIS.items():
        for name in names:
            combo = ctx.widgets.get_or_none(name)
            group = combo.parentWidget() if combo is not None else None
            if group is not None:
                group.setVisible(not nanowells)
        for name in _combo_names(page) + _scope_names(page):
            group = ctx.widgets.get_or_none(name + "_grp")
            if group is not None:
                group.setVisible(nanowells)
    return NANOWELLS if nanowells else "cells"


def _populate(ctx, page: str, table) -> tuple[str, str]:
    """Fill the page's pickers from *table*'s metrics, keeping a choice still offered; returns ``(x key, y key)``."""
    keys = [m.key for m in table.metrics]
    labels = [m.label for m in table.metrics]
    defaults = default_axes(table)
    chosen = []
    for name, default in zip(_combo_names(page), defaults):
        combo = ctx.widgets.get_or_none(name)
        if combo is None:
            chosen.append(default)
            continue
        current = combo.currentData()
        blocked = combo.blockSignals(True)
        try:
            if [combo.itemData(i) for i in range(combo.count())] != keys:
                combo.clear()
                for key, label in zip(keys, labels):
                    combo.addItem(label, key)
            combo.setCurrentIndex(keys.index(current) if current in keys else keys.index(default))
        finally:
            combo.blockSignals(blocked)
        chosen.append(combo.currentData())
    return chosen[0], chosen[1]


def _selected_hours(ctx, page: str):
    selections = getattr(ctx.runtime.scatter, _TP_SELECTIONS[page], None) or {}
    hours = []
    for text, on in selections.items():
        if on:
            try:
                hours.append(float(text))
            except (TypeError, ValueError):
                continue
    return hours if selections else None


def _pin_cells_log_axes(ax, prefs) -> tuple[str, str]:
    """Give the Cells page's axes the figure's own log state and return the two axis-label suffixes.

    The same rules as the per-cell scatter draws by (``gating.render_scatter_points``), because it is the same
    figure and the same Properties toggles: ``scatter_x_log`` / ``scatter_y_log`` (on unless turned off), drawn as
    log(x+1) so an empty nanowell's zero stays on the axis, in the base the panel names. Pinned on the axes so the
    style pass applies these rather than the shared ``x_log`` / ``y_log`` other figures use.
    """
    from review.core.plot_style import apply_axis_log_scale, log_base_value
    from review.features.scatter._layout import scatter_log_suffix

    x_log = bool(prefs.get("scatter_x_log", True))
    y_log = bool(prefs.get("scatter_y_log", True))
    ax._aw_force_xlog, ax._aw_force_ylog, ax._aw_log_plus1 = x_log, y_log, True
    apply_axis_log_scale(ax, "x", x_log, log_base_value(prefs.get("x_log_base", "10")))
    apply_axis_log_scale(ax, "y", y_log, log_base_value(prefs.get("y_log_base", "10")))
    return scatter_log_suffix(prefs, "x"), scatter_log_suffix(prefs, "y")


def redraw(ctx, page: str) -> None:
    """Draw the nanowell scatter on *page* (``"cells"``: one point per nanowell; ``"aggregate"``: per well)."""
    from review.core.plot_style import apply_export_style_to_current, figure_prefs, mark_style_view, tokens_for
    from review.data.observations import nanowell_observations

    ax_name, fig_name, canvas_name = _FIGURE[page]
    ax, fig, canvas = (ctx.widgets.get_or_none(n) for n in (ax_name, fig_name, canvas_name))
    if ax is None or fig is None or canvas is None:
        return
    # Which Properties controls this draw honours: the nanowell points have no density shading (Cells page) and no
    # spread (Wells page), so the panel hides those rows while nanowells are drawn (``_style_facets``).
    mark_style_view(ctx, fig, NANOWELLS)
    for extra in list(fig.axes)[1:]:
        fig.delaxes(extra)
    ax.clear()
    for text in [t for t in fig.texts if t.get_gid() in ("observations_footer", SUBSET_LABEL_GID)]:
        text.remove()
    bg, title_fg, muted, grid, _spine = tokens_for(ax)
    ax.set_facecolor(bg)
    ctx.runtime.scatter.interaction_cache = {"points": []}
    groups = scatter_groups(ctx)
    # The Nanowells page's scope: its excluded wells, its Subset and its Fixed cohort decide which nanowells are
    # drawn here too, so the scatter and the page's views are about the same nanowells.
    table = nanowell_observations(ctx, [w for g in groups for w in g.wells], page_scope=True) if groups else None
    if table is None or not groups:
        if page == "cells":
            ctx.runtime.scatter.gate_render = []
        ax.set_axis_off()
        follows_theme(ax.text(0.5, 0.5, "No wells selected — nothing to plot." if not groups else
                "No nanowell tables for the selected wells.", transform=ax.transAxes, ha="center", va="center",
                color=muted, fontsize=10), muted=True)
        canvas.draw_idle()
        return
    x_key, y_key = _populate(ctx, page, table)
    _populate_scope(ctx, page, table)
    drawn = nanowell_scatter(table, x_key, y_key, groups, timepoints_h=_selected_hours(ctx, page),
                             per_well=page == "aggregate")
    prefs = figure_prefs(ctx, fig)
    for series in drawn.series:
        if len(series.x):
            coll = ax.scatter(series.x, series.y, color=series.color, alpha=0.8, linewidths=0,
                              label=series.label)
            # Marker size, shape and edge are the panel's (``plot_style._style_marker_collections``).
            coll._aw_marker_collection = True
    x_suffix = y_suffix = ""
    if page == "cells":
        x_suffix, y_suffix = _pin_cells_log_axes(ax, prefs)
    ax.set_xlabel(drawn.x_label + x_suffix)
    ax.set_ylabel(drawn.y_label + y_suffix)
    from review.domain.observation import well_statistic

    stats = sorted({well_statistic(table, x_key), well_statistic(table, y_key)})
    what = f"per well ({' / '.join(stats)} over nanowells)" if page == "aggregate" else "per nanowell"
    ax.set_title(f"Nanowells: {drawn.x_label} vs {drawn.y_label} — {what}", color=title_fg)
    ax.grid(True, alpha=0.3, color=grid)
    if any(len(s.x) for s in drawn.series):
        ax.legend(loc="best", fontsize=8, framealpha=0.0, facecolor="none")
    if page == "cells":
        _paint_nanowell_gates(ctx, ax, drawn, x_key, y_key, muted)
        # Each point names its nanowell, so a click shows it on the Nanowells page (``callbacks``).
        ctx.runtime.scatter.interaction_cache["points"] = click_points(drawn)
    lines = [drawn.footer, *drawn.notes]
    if page == "cells" and table.subset and ctx.state.cell_gating.scatter_gates:
        lines.append(f"Gate percentages are of the nanowells drawn: those in the Subset ({table.subset}).")
    footer = "\n".join(lines)
    from review.core.observation_band import write_footer

    write_footer(fig, footer, muted)
    _stamp_subset(ctx, fig, muted)
    apply_export_style_to_current(ctx, fig, canvas)
    if page == "cells":
        # ``ax.clear()`` above voided any armed RectangleSelector; rebuild it on the fresh axes.
        from review.features.scatter.gating import refresh_gate_selector
        refresh_gate_selector(ctx)
    canvas.draw_idle()


def _paint_nanowell_gates(ctx, ax, drawn, x_key: str, y_key: str, ink) -> None:
    """Draw the stored scatter gates over the per-nanowell points, as the cell scatter does over cells.

    The axes are named by the same ``nw::<key>`` columns the per-cell "Nanowell ▸" pickers use, so a gate drawn
    here is stamped with them (``gating._on_gate_drawn`` reads ``interaction_cache``) and means the same box in
    the same space wherever it is drawn: a gate on nanowell count × brightness shows on this plot and on a cell
    plot of "Nanowell ▸ count" × "Nanowell ▸ brightness", and on no other axis pair. Without this the redraw
    that follows a drawn gate cleared it from the screen, and stamped it with no columns at all.

    Each label's percentage is of the group's plotted nanowells, the population the box was drawn over — the
    nanowells inside the Nanowells page's Subset when it has one, which the footer says. A Subset condition that
    names one of these gates therefore leaves its own gate reading near 100 %: the points outside it are not drawn.
    """
    from review.data.observations import nanowell_cell_column
    from review.features.scatter.gating import paint_gates

    cols = (nanowell_cell_column(x_key), nanowell_cell_column(y_key))
    ctx.runtime.scatter.interaction_cache = {"points": [], "col_x": cols[0], "col_y": cols[1]}
    data = {s.label: {"x": s.x, "y": s.y, "color": s.color} for s in drawn.series if len(s.x)}
    # No per-timepoint split in the label: one line per group (or one percentage for one group).
    paint_gates(ctx, [(ax, data, [], ink, True)], cols=cols)


#: The section and label prefix of a cell axis that is the cell's own nanowell's metric.
CELL_NANOWELL_SECTION = "Nanowell (of this cell)"
CELL_NANOWELL_PREFIX = "Nanowell ▸ "


def cell_nanowell_entries(ctx) -> dict[str, str]:
    """``label -> metric key`` for the per-cell pickers' "Nanowell ▸" entries: each cell takes its own nanowell's
    value at its timepoint (``review/data/observations.with_nanowell_columns``). Empty where nanowell observations
    are not available (a standard plate, or a build without the Nanowells feature).

    Only the metric names are needed, so they are read from the tables' header rows (``nanowell_metric_specs``)
    rather than by computing the nanowell table: this runs while the Scatter tab builds, inside the dataset load."""
    from review.data.observations import available_observations, nanowell_metric_specs, observation_groups

    if NANOWELLS not in available_observations(ctx):
        return {}
    wells = [w for _l, ws, _c in observation_groups(ctx) for w in ws] or list(ctx.state.dataset.well_paths)[:1]
    metrics = nanowell_metric_specs(ctx, wells) if wells else None
    if metrics is None:
        return {}
    return {f"{CELL_NANOWELL_PREFIX}{m.label}": m.key for m in metrics}


def cell_axis_column(ctx, entry: str) -> str | None:
    """The joined column for a "Nanowell ▸" entry, ``None`` for any other entry."""
    from review.data.observations import nanowell_cell_column

    if not str(entry).startswith(CELL_NANOWELL_PREFIX):
        return None
    key = cell_nanowell_entries(ctx).get(entry)
    return nanowell_cell_column(key) if key else None


def drew_nanowells(ctx, page: str) -> bool:
    """Under Observations: Nanowells draw *page* from nanowell rows and return ``True``; else sync the pickers
    back to the cell ones and return ``False`` so the caller draws cells."""
    if sync_controls(ctx) != NANOWELLS:
        from review.core.plot_style import mark_style_view

        mark_style_view(ctx, ctx.widgets.get_or_none(_FIGURE[page][1]), "")   # the cell view: every control again
        return False
    redraw(ctx, page)
    return True


def add_cell_nanowell_section(ctx, sections: list, options: list) -> list:
    """Append the "Nanowell (of this cell)" section to the cell pickers' *sections*; return the options with it.

    On a nanowell plate each cell can be plotted against its own nanowell's metrics (cell GFP against the
    nanowell's crowding, say) — offered only when the Nanowells feature supplies them."""
    labels = list(cell_nanowell_entries(ctx))
    if labels:
        sections.append((CELL_NANOWELL_SECTION, labels))
    return list(options) + labels


def stamp_cells_footer(ctx, fig) -> None:
    from review.core.observation_band import stamp_cells_footer as _stamp

    _stamp(ctx, fig)
