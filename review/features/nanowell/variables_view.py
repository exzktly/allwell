"""The Nanowells page's *Group by* combo and *Decorate* popover: the sample-variable controls every plot has.

The Nanowells page is an Analysis page, not a Plotting leaf, so it never shows the plotting band — and the band is
where *Group by* and *Decorate* live. The page already pooled through the band's setting
(``controller.page_groups`` reads ``plot_members.plot_replicate_sets``), so a *Group by* chosen on Bar Plots
regrouped this page with nothing here saying so or offering to change it, and *Colour by* / *Split by* had no
route to its figures at all.

**The same settings, not a second copy.** Each control writes the ``PlotState`` field the band's twin writes,
through the same ``group_by_controller`` setters, and is filled by the same ``fill_*`` functions — so the two
surfaces list the same variables, fall back the same way when one is deleted, and can never hold different
answers. *Group by* is cross-tab by design (every plot pools through one seam); a page with a grouping of its own
would be a figure that disagreed with the Bar tab's about what a group is.

Both are hidden until the plate has a variable, as on the band: a control whose only option is the status quo is
not a control. *Decorate* shows only on the views that draw it (``controller.DECORATED_VIEWS``).
"""

from __future__ import annotations

from types import SimpleNamespace

from core.safe import swallow
from review.features.nanowell import controller as nwc
from widgets.labeled_combo import LabeledComboBox

#: The page's widget names for the three decoration combos, keyed by the ``PlotState`` field each writes.
DECORATION_WIDGETS = {
    "colour_by_variable": "_nw_colourby_cb",
    "split_by_variable": "_nw_splitby_cb",
    "sub_split_by_variable": "_nw_subsplitby_cb",
}

_GROUP_BY_TIP = ("Pool the page's wells by a sample variable (Home → Sample Map → Variables) instead of by your "
                 "replicate sets: one group per value.\nThe same setting as Group by on the Plotting tabs — changing "
                 "it here changes every plot.")
_DECORATE_TIP = "Colour and split the Distribution view by a sample variable"

#: What the page's *Colour by* calls "do not colour by a variable": its dots go back to the occupancy colours, not to
#: the plate colours the band's combo names.
NO_COLOUR_LABEL = "Occupancy"


def build_variable_controls(ctx, band) -> SimpleNamespace:
    """Add *Group by* to *band*'s scope zone and *Decorate ▾* to its actions; return the refresh hook.

    ``refresh(view)`` refills every combo from the state and shows each control where it does something; the
    page's ``refresh_controls`` calls it, so a change made on the plotting band is on these combos the next time
    the page draws.
    """
    from review.core.group_by_controller import DECORATION_CONTROLS, DECORATION_TITLES
    from review.core.ui_helpers import make_popover_button

    reg = ctx.widgets
    group_cb = reg.register("_nw_groupby_cb", LabeledComboBox("Group by"))
    group_cb.setToolTip(_GROUP_BY_TIP)
    group_cb.currentIndexChanged.connect(lambda _i: on_group_by(ctx, group_cb.currentData() or ""))
    group_grp = reg.register("_nw_groupby_grp", band.add_scope("", group_cb))
    group_grp.setVisible(False)

    button, _panel, panel_layout = make_popover_button("Decorate ▾", band)
    button.setToolTip(_DECORATE_TIP)
    reg.register("_nw_decorate_btn", button)
    combos = {}
    for field, _band_widget, _label in DECORATION_CONTROLS:
        combo = reg.register(DECORATION_WIDGETS[field], LabeledComboBox(DECORATION_TITLES[field]))
        combo.currentIndexChanged.connect(
            lambda _i, f=field, c=combo: on_decoration(ctx, f, c.currentData() or ""))
        panel_layout.addWidget(combo)
        combos[field] = combo
    band.add_action(button)
    button.setVisible(False)

    def refresh(view: str) -> None:
        from review.core.group_by_controller import fill_decoration_combos, fill_group_by_combo

        has_variables = fill_group_by_combo(ctx, group_cb)
        fill_decoration_combos(ctx, combos, {"colour_by_variable": NO_COLOUR_LABEL})
        group_grp.setVisible(has_variables)
        button.setVisible(has_variables and view in nwc.DECORATED_VIEWS)

    return SimpleNamespace(group_by=group_cb, group=group_grp, decorate=button, combos=combos, refresh=refresh,
                           signalling=(group_cb, *combos.values()))


def on_group_by(ctx, variable_id: str) -> None:
    """Apply *Group by* from the page: the shared setting, then the page — which may now draw other wells.

    The band's combo is refilled too, so the Plotting tabs show the grouping in force rather than the one chosen
    there last. ``nw_on_selection_changed`` rather than a bare redraw: a grouping can bring wells onto the page
    whose tables have not been read, and that handler reads them.
    """
    from review.core.group_by_controller import refresh_group_by_combo, set_group_by_variable

    with swallow("apply Group by from the Nanowells page"):
        set_group_by_variable(ctx, variable_id)
        refresh_group_by_combo(ctx)
        nwc.nw_on_selection_changed(ctx)


def on_decoration(ctx, field: str, variable_id: str) -> None:
    """Apply *Colour by*, *Split by* or *Then by* from the page: the shared setting, the band's combos, the page."""
    from review.core.group_by_controller import refresh_decoration_combos, set_decoration_variable

    with swallow("apply a decoration from the Nanowells page"):
        set_decoration_variable(ctx, field, variable_id)
        refresh_decoration_combos(ctx)
        nwc.redraw(ctx)                  # refreshes the page's controls first: *Then by* follows *Split by*
