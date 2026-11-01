"""The Plotting band's **Observations: Cells | Nanowells** toggle — what a plot's points are.

Shown first in the band, because it changes what every other control lists. When it is offered is decided, never
written down tab by tab (CLAUDE.md §3):

* a **standard plate** shows nothing — there is nothing else to choose;
* a **nanowell dataset in a build with the Nanowells feature**, on a leaf whose registry descriptor declares
  ``observations`` holding ``"nanowells"``, shows the live toggle;
* the same dataset on a leaf that declares cells only shows the static text "Observations: cells — no nanowell view
  for this plot", so a control that cannot apply is not offered and its absence is explained;
* a nanowell dataset in a build **without** the Nanowells feature shows nothing; its cell plots say in their footer
  that the nanowell views are not in this build (``observation.pooled_cells_line``).

The choice is ``ctx.state.plot.observations``; :func:`effective_observations` is what a leaf draws — cells on a leaf
that cannot draw nanowells, whatever the toggle last said on another leaf. Core: imports no feature.
"""

from __future__ import annotations

from widgets.plot_card import follows_theme

from review.domain.observation import CELLS, NANOWELLS, OBSERVATION_LABELS

#: Leaves whose axes choose their own nanowell metrics (Scatter's X / Y pickers), so the band's Metric is not shown.
OWN_AXES_LEAVES = frozenset({"Scatter Plot"})
#: The static text on a nanowell dataset's leaf that draws cells only.
CELLS_ONLY_TEXT = "Observations: cells — no nanowell view for this plot"


def leaf_observations(ctx, title: str) -> tuple[str, ...]:
    """What the Plotting leaf *title* declares it can draw (its registry descriptor), cells when undeclared."""
    features = getattr(ctx, "features", None)
    descriptor = features.get(title) if features is not None else None
    return tuple(getattr(descriptor, "observations", (CELLS,)) or (CELLS,))


def effective_observations(ctx, title: str) -> str:
    """What the leaf *title* draws now: the toggle's choice when this dataset, build and leaf all allow it."""
    from review.data.observations import available_observations

    chosen = getattr(ctx.state.plot, "observations", CELLS)
    if chosen == NANOWELLS and NANOWELLS in available_observations(ctx) and NANOWELLS in leaf_observations(ctx, title):
        return NANOWELLS
    return CELLS


def band_mode(ctx, title: str) -> str:
    """``"hidden"``, ``"toggle"`` or ``"cells_only"`` — what the band shows for the leaf *title*."""
    from review.data.observations import available_observations

    if NANOWELLS not in available_observations(ctx):
        return "hidden"
    return "toggle" if NANOWELLS in leaf_observations(ctx, title) else "cells_only"


def build_observation_controls(ctx, band) -> None:
    """Add the toggle and its static stand-in to *band*'s scope zone, hidden until :func:`refresh` shows one.

    Called first when the band is built, so the group sits at the start of the row."""
    from PySide6.QtWidgets import QLabel

    from widgets.segmented_control import SegmentedControl

    toggle = SegmentedControl()
    for key in (CELLS, NANOWELLS):
        toggle.addSegment(OBSERVATION_LABELS[key], data=key)

    def _on_change(_idx: int) -> None:
        set_observations(ctx, toggle.currentData())

    toggle.currentChanged.connect(_on_change)
    ctx.widgets.register("_plotting_observations_toggle", toggle)
    group = band.add_scope("Observations", toggle)
    group.setVisible(False)
    ctx.widgets.register("_plotting_observations_grp", group)
    static = QLabel(CELLS_ONLY_TEXT)
    static_group = band.add_scope("", static)
    static_group.setVisible(False)
    ctx.widgets.register("_plotting_observations_static", static_group)
    # Under Nanowells, one Metric picker stands in for Channel / Property / ON threshold (they describe a cell).
    from widgets.labeled_combo import LabeledComboBox

    metric = ctx.widgets.register("_plotting_nw_metric_cb", LabeledComboBox("Metric"))

    def _on_metric(_idx: int) -> None:
        key = metric.currentData()
        if key and key != getattr(ctx.state.plot, "nanowell_metric", ""):
            ctx.state.plot.nanowell_metric = key
            from review.core.plot_orchestrator import redraw_every_scope

            redraw_every_scope(ctx)

    metric.currentIndexChanged.connect(_on_metric)
    metric_group = band.add_scope("", metric)
    metric_group.setVisible(False)
    ctx.widgets.register("_plotting_nw_metric_grp", metric_group)


def set_observations(ctx, observations: str) -> None:
    """Record the choice and redraw every plot, so each leaf draws what it now means."""
    if observations not in (CELLS, NANOWELLS) or getattr(ctx.state.plot, "observations", CELLS) == observations:
        return
    ctx.state.plot.observations = observations
    from review.core.plot_orchestrator import redraw_every_scope

    redraw_every_scope(ctx)


def refresh(ctx, title: str) -> None:
    """Show the toggle, its static text, or neither, for the Plotting leaf *title*; mirror the recorded choice.

    Never raises: the band is chrome, and a failure here must not stop a tab switch or a load (it is logged)."""
    from core.safe import swallow

    with swallow("show the Observations toggle for this plot"):
        _refresh(ctx, title)


def _refresh(ctx, title: str) -> None:
    mode = band_mode(ctx, title)
    group = ctx.widgets.get_or_none("_plotting_observations_grp")
    static = ctx.widgets.get_or_none("_plotting_observations_static")
    toggle = ctx.widgets.get_or_none("_plotting_observations_toggle")
    if group is not None:
        group.setVisible(mode == "toggle")
    if static is not None:
        static.setVisible(mode == "cells_only")
    if toggle is not None:
        blocked = toggle.blockSignals(True)
        try:
            toggle.setCurrentByData(getattr(ctx.state.plot, "observations", CELLS))
        finally:
            toggle.blockSignals(blocked)
    # A leaf drawing nanowells with the band's scope (not Scatter, whose axes pick their own) swaps the cell
    # controls for the Metric picker; the cell controls come back on any other leaf or under Cells.
    band_metric = effective_observations(ctx, title) == NANOWELLS and title not in OWN_AXES_LEAVES
    metric_group = ctx.widgets.get_or_none("_plotting_nw_metric_grp")
    if metric_group is not None:
        metric_group.setVisible(band_metric)
    if band_metric:
        for name in ("_plotting_channel_grp", "_plotting_metric_grp", "_plotting_thresh_frac_grp"):
            grp = ctx.widgets.get_or_none(name)
            if grp is not None:
                grp.setVisible(False)
        populate_metric_picker(ctx)


def refresh_current(ctx) -> None:
    """:func:`refresh` for whichever Plotting leaf is current — a dataset load changes what may be offered."""
    stack = ctx.widgets.get_or_none("_plotting_notebook")
    title = stack.currentName() if stack is not None else ""
    if title:
        refresh(ctx, title)


#: The gid of the footer line a plot carries about its observations.
FOOTER_GID = "observations_footer"


def stamp_cells_footer(ctx, fig, wells=None) -> str:
    """On a nanowell dataset, write the cell plot's footer onto *fig* (and return it); ``""`` elsewhere.

    "Observations: cells · pooled from N occupied nanowells per well", plus "· nanowell views are not in this
    build" when the build has no Nanowells feature — so a cell plot of a nanowell plate never reads as an ordinary
    one, in any build, on screen or exported. The previous footer is removed first; a redraw writes one line.
    """
    from review.data.observations import is_nanowell_dataset, nanowell_views_available, occupied_nanowells_by_well
    from review.domain.observation import pooled_cells_line

    for text in [t for t in getattr(fig, "texts", []) if t.get_gid() == FOOTER_GID]:
        text.remove()
    if fig is None or not is_nanowell_dataset(ctx):
        return ""
    if wells is None:
        wells = getattr(getattr(ctx.state, "selection", None), "selected_wells", None) or ()
    line = pooled_cells_line(occupied_nanowells_by_well(ctx, wells), nanowell_views=nanowell_views_available(ctx))
    from review.core.plot_style import tokens_for

    axes = getattr(fig, "axes", [])
    muted = tokens_for(axes[0])[2] if axes else None
    follows_theme(fig.text(0.01, 0.002, line, ha="left", va="bottom", fontsize=7, color=muted, gid=FOOTER_GID), muted=True)
    return line


#: What a nanowell figure says when the band's Fold change menu is on: it normalises cell plots, not these.
FOLD_CHANGE_NOT_APPLIED = ("The Fold change menu normalises cell plots; for nanowells pick a fold-change metric "
                           "(against the Nanowells page's Baseline).")


def nanowell_table_for(ctx, wells):
    """The nanowell observations of *wells*, or ``None`` (``review/data/observations``)."""
    from review.data.observations import nanowell_observations

    return nanowell_observations(ctx, wells) if wells else None


def chosen_metric(ctx, table) -> str:
    """The band's nanowell metric, defaulting to the cell count when unset or not in *table*."""
    keys = [m.key for m in table.metrics] if table is not None else []
    key = getattr(ctx.state.plot, "nanowell_metric", "") or ""
    if key in keys:
        return key
    return "n" if "n" in keys else (keys[0] if keys else "")


def populate_metric_picker(ctx) -> None:
    """Fill the band's Metric picker from the selected wells' nanowell metrics, keeping the choice."""
    from review.data.observations import observation_groups

    combo = ctx.widgets.get_or_none("_plotting_nw_metric_cb")
    if combo is None:
        return
    wells = [w for _label, ws, _c in observation_groups(ctx) for w in ws]
    table = nanowell_table_for(ctx, wells)
    if table is None:
        return
    keys = [m.key for m in table.metrics]
    blocked = combo.blockSignals(True)
    try:
        if [combo.itemData(i) for i in range(combo.count())] != keys:
            combo.clear()
            for m in table.metrics:
                combo.addItem(m.label, m.key)
        key = chosen_metric(ctx, table)
        combo.setCurrentIndex(keys.index(key) if key in keys else 0)
    finally:
        combo.blockSignals(blocked)
