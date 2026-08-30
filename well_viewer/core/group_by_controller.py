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
    from well_viewer.domain.plot_members import wells_without_variable

    variable_id = str(getattr(ctx.state.plot, "group_by_variable", "") or "")
    if not variable_id:
        return ""
    vset = getattr(ctx.state.selection, "variables", None)
    variable = vset.by_id(variable_id) if vset is not None else None
    if variable is None:
        return ""
    missing = wells_without_variable(ctx, variable_id)
    if not missing:
        return ""
    shown = ", ".join(missing[:6])
    more = f" and {len(missing) - 6} more" if len(missing) > 6 else ""
    return (f"Grouped by {variable.name}: {len(missing)} well(s) have no value "
            f"and are not plotted — {shown}{more}.")


def _announce(ctx) -> None:
    message = group_by_shortfall(ctx)
    if not message:
        return
    with swallow("report the Group by shortfall"):
        ctx.notify.set_status(message)
    _logger.info("%s", message)
