"""Scatter under **Observations: Nanowells** — the axis pickers and the redraw (NANOWELL_OBSERVATIONS_TRACKER #9).

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
from review.features.scatter.observation_compute import default_axes, nanowell_scatter

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


def build_axis_controls(ctx, slot, page: str) -> None:
    """Add the page's nanowell X / Y metric pickers to its band *slot*, hidden; :func:`sync_controls` shows them."""
    from widgets.labeled_combo import LabeledComboBox

    for name, caption in zip(_combo_names(page), ("X", "Y")):
        combo = ctx.widgets.register(name, LabeledComboBox(caption))
        combo.currentIndexChanged.connect(lambda _i, _p=page: _redraw_page(ctx, _p))
        group = slot.add_scope("", combo)
        group.setVisible(False)
        ctx.widgets.register(name + "_grp", group)


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
        for name in _combo_names(page):
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


def redraw(ctx, page: str) -> None:
    """Draw the nanowell scatter on *page* (``"cells"``: one point per nanowell; ``"aggregate"``: per well)."""
    from review.core.plot_style import apply_export_style_to_current, tokens_for
    from review.data.observations import nanowell_observations

    ax_name, fig_name, canvas_name = _FIGURE[page]
    ax, fig, canvas = (ctx.widgets.get_or_none(n) for n in (ax_name, fig_name, canvas_name))
    if ax is None or fig is None or canvas is None:
        return
    for extra in list(fig.axes)[1:]:
        fig.delaxes(extra)
    ax.clear()
    for text in [t for t in fig.texts if t.get_gid() == "observations_footer"]:
        text.remove()
    bg, title_fg, muted, grid, _spine = tokens_for(ax)
    ax.set_facecolor(bg)
    ctx.runtime.scatter.interaction_cache = {"points": []}
    groups = scatter_groups(ctx)
    table = nanowell_observations(ctx, [w for g in groups for w in g.wells]) if groups else None
    if table is None or not groups:
        ax.set_axis_off()
        follows_theme(ax.text(0.5, 0.5, "No wells selected — nothing to plot." if not groups else
                "No nanowell tables for the selected wells.", transform=ax.transAxes, ha="center", va="center",
                color=muted, fontsize=10), muted=True)
        canvas.draw_idle()
        return
    x_key, y_key = _populate(ctx, page, table)
    drawn = nanowell_scatter(table, x_key, y_key, groups, timepoints_h=_selected_hours(ctx, page),
                             per_well=page == "aggregate")
    size = 30 if page == "aggregate" else 12
    for series in drawn.series:
        if len(series.x):
            ax.scatter(series.x, series.y, s=size, color=series.color, alpha=0.8, linewidths=0,
                       label=series.label)
    ax.set_xlabel(drawn.x_label)
    ax.set_ylabel(drawn.y_label)
    what = "per well (median over nanowells)" if page == "aggregate" else "per nanowell"
    ax.set_title(f"Nanowells: {drawn.x_label} vs {drawn.y_label} — {what}", color=title_fg)
    ax.grid(True, alpha=0.3, color=grid)
    if any(len(s.x) for s in drawn.series):
        ax.legend(loc="best", fontsize=8, framealpha=0.0, facecolor="none")
    footer = "\n".join([drawn.footer, *drawn.notes])
    follows_theme(fig.text(0.01, 0.005, footer, ha="left", va="bottom", fontsize=7, color=muted, gid="observations_footer"), muted=True)
    apply_export_style_to_current(ctx, fig, canvas)
    canvas.draw_idle()


#: The section and label prefix of a cell axis that is the cell's own nanowell's metric.
CELL_NANOWELL_SECTION = "Nanowell (of this cell)"
CELL_NANOWELL_PREFIX = "Nanowell ▸ "


def cell_nanowell_entries(ctx) -> dict[str, str]:
    """``label -> metric key`` for the per-cell pickers' "Nanowell ▸" entries: each cell takes its own nanowell's
    value at its timepoint (``review/data/observations.with_nanowell_columns``). Empty where nanowell observations
    are not available (a standard plate, or a build without the Nanowells feature)."""
    from review.data.observations import available_observations, nanowell_observations, observation_groups

    if NANOWELLS not in available_observations(ctx):
        return {}
    wells = [w for _l, ws, _c in observation_groups(ctx) for w in ws] or list(ctx.state.dataset.well_paths)[:1]
    table = nanowell_observations(ctx, wells) if wells else None
    if table is None:
        return {}
    return {f"{CELL_NANOWELL_PREFIX}{m.label}": m.key for m in table.metrics}


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
