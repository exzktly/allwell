"""The Group-by-variable band control: fill it, apply it, keep it honest.

The setting itself is one string on ``PlotState`` and the substitution happens
in ``domain/plot_members.plot_replicate_sets``. This module is the shell side —
what the combo lists, what happens when it changes, and the sentence that says
what the choice left out.

Kept out of any feature package because the control is cross-tab: every plot
pools its wells through the same seam, so a Bar-tab-owned version would be a
setting one tab could hold while the others did not.
"""

from __future__ import annotations

import logging

from well_viewer.core.safe import swallow

_logger = logging.getLogger("well_viewer")

#: What the combo calls "do not group by a variable".
NO_VARIABLE_LABEL = "Wells / groups"

#: What the two popover combos call "do not do this".
NO_COLOUR_LABEL = "Plate colours"
NO_SPLIT_LABEL = "One band"

#: The centre tabs whose renderer actually reads *Colour by* and *Split by*.
#:
#: The two combos live on the page-level plotting band, which every Plotting
#: leaf shares — and a control added to the band directly is never hidden by the
#: per-leaf slot machinery. So they appeared on Bar Plots, Line Graphs, Response
#: Curves, Scatter, Distribution and Heat Map, and exactly one of those six
#: draws through ``features/barplot/_decorate``. On the other five, picking a
#: variable wrote the state, emitted ``plot_options_changed``, redrew every tab
#: and changed nothing on screen, with nothing saying why: §7.6's control that
#: exists and does not work, against the band's own rule — quoted two comments
#: above the code that broke it — that it "only ever carries controls that do
#: something".
#:
#: A roster rather than a hardcoded ``== "Bar Plots"`` so that adding the second
#: renderer is one line here, and so the reason is written where the rule is.
#: *Group by* is deliberately **not** gated: every tab pools through
#: ``plot_replicate_sets``, so it really does do something everywhere.
DECORATED_TABS = frozenset({"Bar Plots"})

#: ``(state field, widget name, "no" label)`` for the two decoration controls.
#: A roster rather than two hand-written blocks: they differ only in those three
#: strings, and the version that had two blocks grew a difference in the third
#: — one of them cleared its state when its variable was deleted and the other
#: did not, so a deleted variable stayed live in the plot with nothing offering
#: it in the combo.
#:
#: ``refresh_group_by_combo`` runs the same fill-and-clear loop and is
#: deliberately **not** in this table. It differs in more than the three strings:
#: it lives in the scope zone rather than the popover, it hides its own group
#: rather than the shared Decorate button, and its visibility does not depend on
#: which tab is showing. Folding it in would mean a fourth and fifth column used
#: by one row each — the registry-for-two-things shape, one row further on.
DECORATION_CONTROLS = (
    ("colour_by_variable", "_plotting_colourby_cb", NO_COLOUR_LABEL),
    ("split_by_variable", "_plotting_splitby_cb", NO_SPLIT_LABEL),
)


def _combo(ctx):
    return ctx.widgets.get_or_none("_plotting_groupby_cb")


def refresh_group_by_combo(ctx) -> None:
    """List the plate's variables, and hide the control when there are none.

    The band only ever carries controls that do something, and a *Group by*
    offering nothing but the status quo is not one. Hidden rather than
    disabled for the same reason the unit field on a text variable is disabled
    rather than hidden: there, greying out states a rule the user can act on;
    here there is no rule to state, only an absence.

    A variable that was selected and has since been deleted falls back to the
    first entry, and the plots fall back with it — ``plot_replicate_sets``
    makes the same choice, so the combo and the plot cannot disagree.
    """
    combo = _combo(ctx)
    if combo is None:
        return
    vset = getattr(ctx.state.selection, "variables", None)
    variables = list(getattr(vset, "variables", ()) or ())
    keep = str(getattr(ctx.state.plot, "group_by_variable", "") or "")

    combo.blockSignals(True)
    combo.clear()
    combo.addItem(NO_VARIABLE_LABEL, "")
    for variable in variables:
        combo.addItem(variable.name, variable.id)
    index = combo.findData(keep)
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)

    if index < 0 and keep:
        # The selection is gone. Clear the state too, or the next redraw reads a
        # variable the combo no longer offers and the two describe different
        # plots.
        ctx.state.plot.group_by_variable = ""

    group = ctx.widgets.get_or_none("_plotting_groupby_grp")
    if group is not None:
        group.setVisible(bool(variables))
    refresh_decoration_combos(ctx)


def refresh_decoration_combos(ctx) -> None:
    """Fill *Colour by* and *Split by*, and hide the popover with no variables.

    Both list every variable, including the one *Group by* holds. Colouring a
    dose series by dose is redundant rather than wrong, and splitting by the
    variable already grouped on gives one member per band — a legible, if
    pointless, figure. Refusing the combination would be this module deciding
    what a user may look at; there is nothing incorrect to prevent.
    """
    for field, name, no_label in DECORATION_CONTROLS:
        combo = ctx.widgets.get_or_none(name)
        if combo is None:
            continue
        vset = getattr(ctx.state.selection, "variables", None)
        variables = list(getattr(vset, "variables", ()) or ())
        keep = str(getattr(ctx.state.plot, field, "") or "")

        combo.blockSignals(True)
        combo.clear()
        combo.addItem(no_label, "")
        for variable in variables:
            combo.addItem(variable.name, variable.id)
        index = combo.findData(keep)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

        if index < 0 and keep:
            # The variable is gone. Cleared here as well as in the combo, or the
            # next redraw decorates by a variable nothing offers.
            setattr(ctx.state.plot, field, "")

    button = ctx.widgets.get_or_none("_plotting_decorate_btn")
    if button is not None:
        vset = getattr(ctx.state.selection, "variables", None)
        has_variables = bool(getattr(vset, "variables", ()) or ())
        button.setVisible(has_variables and _current_tab_decorates(ctx))


def _current_tab_decorates(ctx) -> bool:
    """Whether the tab on screen draws through the decoration seam.

    Read at refresh time rather than cached: the band is shared, so the answer
    changes on every tab switch and a cached one would show the control on the
    tab the user just left.
    """
    from well_viewer.core.tab_controller import current_centre_tab

    with swallow("read the current centre tab for the Decorate button"):
        return current_centre_tab(ctx) in DECORATED_TABS
    return False


def set_decoration_variable(ctx, field: str, variable_id: str) -> None:
    """Apply *Colour by* or *Split by* and redraw every tab.

    Through the bus for the same reason *Group by* is: these are cross-tab
    settings, and a direct redraw would leave the other tabs coloured the
    previous way until they were touched.

    No shortfall sentence, and that is the difference between these and
    *Group by*: grouping by a variable **drops** every well with no value for
    it, while colouring or splitting by one keeps them and gives them their own
    named ink and their own named band. Nothing is left out, so there is
    nothing to report — and a sentence saying so anyway would train the reader
    to ignore the one that matters.
    """
    if field not in {name for name, _widget, _label in DECORATION_CONTROLS}:
        return
    setattr(ctx.state.plot, field, str(variable_id or ""))
    with swallow("request a redraw after a plot decoration change"):
        ctx.bus.plot_options_changed.emit(ctx.state.plot)


def set_group_by_variable(ctx, variable_id: str) -> None:
    """Apply the choice, report what it leaves out, and redraw everything."""
    ctx.state.plot.group_by_variable = str(variable_id or "")
    _announce(ctx)
    # Through the bus rather than by calling a redraw directly: every tab pools
    # its wells through the same seam, so every tab's picture changed, and
    # ``RedrawCoordinator`` already coalesces ``plot_options_changed`` into one
    # pass over the visible scope with the rest deferred. Redrawing one tab here
    # would leave the others showing the previous grouping until touched — which
    # is the two-tabs-disagreeing failure this whole seam exists to prevent.
    with swallow("request a redraw after a Group by change"):
        ctx.bus.plot_options_changed.emit(ctx.state.plot)


def group_by_shortfall(ctx) -> str:
    """``""``, or the sentence naming the wells this grouping leaves out.

    **The shortfall has to be said.** Grouping by a variable drops every well
    with no value for it, and a plate that silently loses eight wells looks
    exactly like a plate that had eighty-eight — the numbers move and nothing
    on screen accounts for it. §7's rule, in the one place a user is most
    likely to be comparing two plots.
    """
    from well_viewer.domain.plot_members import (
        variable_replicate_sets, wells_the_grouping_adds,
        wells_without_variable,
    )

    variable_id = str(getattr(ctx.state.plot, "group_by_variable", "") or "")
    if not variable_id:
        return ""
    vset = getattr(ctx.state.selection, "variables", None)
    variable = vset.by_id(variable_id) if vset is not None else None
    if variable is None:
        return ""

    # **Which sentence is true depends on whether the grouping applied at all.**
    # ``plot_replicate_sets`` falls back to the user's own sets when the chosen
    # variable groups nothing, rather than emptying every plot in the app — so
    # in that case the wells with no value *are* on the figure, and the band
    # still reads "Group by: Dose" over bars named after the user's groups.
    # Saying "not plotted" there would be a sentence contradicting the picture
    # it is printed on, which is worse than saying nothing.
    if not variable_replicate_sets(ctx, variable_id):
        return (f"Group by {variable.name} matched no wells — "
                f"showing your own groups instead.")

    # **The addition, not only the subtraction.** With no replicate sets defined
    # the plate's selection is what drives every plot — and grouping by a
    # variable ignores it, pooling every *loaded* well that holds a value. One
    # well selected, four drawn. That is the defensible semantics of "group the
    # plate by this variable", but it is still a substitution the user did not
    # ask for on that click, and the sentence reported only what grouping takes
    # away.
    added = wells_the_grouping_adds(ctx, variable_id)
    if added:
        return (f"Grouped by {variable.name}: every well with a value is "
                f"pooled, including {len(added)} outside your selection.")

    missing = wells_without_variable(ctx, variable_id)
    if not missing:
        return ""
    shown = ", ".join(missing[:6])
    more = f" and {len(missing) - 6} more" if len(missing) > 6 else ""
    return (f"Grouped by {variable.name}: {len(missing)} well(s) have no value "
            f"and are not plotted — {shown}{more}.")


def _announce(ctx) -> None:
    """Log the shortfall; the figure is what shows it.

    **Not on the status line.** ``notify.set_status`` writes the *persistent*
    bar, and a redraw that has nothing to report has no reason to write
    anything — so the sentence outlived its condition: fill in the missing
    wells, the figure's title clears, and the bar goes on naming them,
    contradicting the plot above it with no redraw able to correct it. The same
    defect the bar tab's significance note was moved off the status line for.
    ``group_by_shortfall`` reaches the figure title, which travels with a saved
    file and is cleared by the redraw that resolves it.
    """
    message = group_by_shortfall(ctx)
    if message:
        _logger.info("%s", message)
