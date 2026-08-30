"""Response Curves orchestration — fill the X combo, redraw, export.

Every rule about what a curve contains is in ``collect``; every rule about the
axes is in ``response_compute`` and ``renderer``. This is the wiring, and it is
kept thin on purpose: a rule that lives in a signal handler is a rule nothing
tests.
"""

from __future__ import annotations

import logging

from well_viewer.core.csv_export import write_csv_rows
from well_viewer.core.safe import swallow
from well_viewer.domain.sample_variables import NUMBER
from well_viewer.features.response.collect import (
    ResponseData, collect_response, omission_caption,
)
from well_viewer.features.response.renderer import draw_response

_logger = logging.getLogger("well_viewer")

#: What the X combo says when the plate has no Number variable to offer. Names
#: the fix rather than the lack: a user here has no way to guess that the answer
#: lives on a sub-tab of a different pane.
NO_VARIABLE_HINT = "No Number variable — define one on Home → Variables"


def _widget(ctx, name):
    return ctx.widgets.get_or_none(name)


def numeric_variables(ctx) -> list:
    """The plate's Number variables, which are the only ones with an axis.

    A Text variable groups and colours perfectly well; it just has nowhere to
    sit on a numeric x, and offering it here would produce a plot whose x order
    was the order someone happened to type the values in.
    """
    vset = getattr(ctx.state.selection, "variables", None)
    return [v for v in (getattr(vset, "variables", ()) or ())
            if v.kind == NUMBER]


def selected_variable_id(ctx) -> str:
    combo = _widget(ctx, "_response_x_cb")
    return (combo.currentData() or "") if combo is not None else ""


def refresh_x_combo(ctx) -> None:
    """List the Number variables, keeping the user's pick where it survives."""
    combo = _widget(ctx, "_response_x_cb")
    if combo is None:
        return
    variables = numeric_variables(ctx)
    keep = combo.currentData()

    combo.blockSignals(True)
    combo.clear()
    if not variables:
        # A disabled combo carrying the reason, rather than an empty one: an
        # empty dropdown says the app is broken, and this state is ordinary on
        # a plate whose variables are all text.
        combo.addItem(NO_VARIABLE_HINT, "")
        combo.setEnabled(False)
    else:
        combo.setEnabled(True)
        for variable in variables:
            label = (f"{variable.name} ({variable.unit})" if variable.unit
                     else variable.name)
            combo.addItem(label, variable.id)
    index = combo.findData(keep)
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


def timepoints_for(ctx) -> list[float]:
    """Which timepoints to draw — the shared focus one, **read without writing**.

    Writing ``focus_timepoint`` from here would move Heat Map and Distribution
    to whatever this tab happened to open on. Defaulting to *all* timepoints
    instead would open a twelve-timepoint plate as twelve overlapping curves per
    condition, which is not a plot anyone can read.
    """
    focus = getattr(ctx.state.plot, "focus_timepoint", None)
    if focus is not None:
        return [float(focus)]
    with swallow("read the dataset's timepoints for the response curve"):
        from well_viewer.data.data_loading import get_all_timepoints
        allt = get_all_timepoints(ctx)
        if allt:
            return [float(allt[0])]
    return [0.0]


def response_data(ctx):
    """The curves for the current X and timepoints."""
    variable_id = selected_variable_id(ctx)
    if not variable_id:
        return ResponseData()
    return collect_response(ctx, variable_id, timepoints_for(ctx))


def redraw_response(ctx) -> None:
    """Repaint the figure. Safe before the tab is built."""
    ax = _widget(ctx, "_response_ax")
    canvas = _widget(ctx, "_response_canvas")
    if ax is None or canvas is None:
        return
    refresh_x_combo(ctx)
    data = response_data(ctx)

    draw_response(ctx, ax, data)

    from well_viewer.core.plot_style import apply_export_style_to_current
    apply_export_style_to_current(ctx, ax.figure, canvas)
    canvas.draw_idle()
    _announce(ctx, data)


def on_x_variable_changed(ctx) -> None:
    redraw_response(ctx)


def _announce(ctx, data) -> None:
    """Put the shortfall on the status line as well as on the axes.

    Three places, as the plan asks: the caption travels with a saved figure, the
    CSV row survives being opened in Excel, and the status line is what the user
    reading the screen right now actually sees.
    """
    caption = omission_caption(data)
    if not caption:
        return
    with swallow("report what the response curve left out"):
        ctx.notify.set_status(caption)


def export_response_csv(ctx) -> None:
    """Write the drawn points, **and a row per member that was left out**.

    The omitted rows carry an empty x and a reason, because a CSV of eight
    groups that lists five is the same silent substitution as a figure of eight
    groups that draws five — and a spreadsheet is exactly where someone counts
    rows and believes the total.
    """
    data = response_data(ctx)
    if not data.curves and not data.omitted and not data.non_numeric:
        # Refused rather than written empty. A CSV with a header and no rows is
        # indistinguishable from a plate that genuinely had nothing to say, and
        # the commonest way to get here is that no X is picked yet — which is a
        # thing the user can fix in one click if they are told.
        with swallow("report that there is nothing to export"):
            ctx.notify.set_status(
                "Nothing to export — pick a Number variable under X first.")
        return
    header = ["member", "timepoint", "x", "x_label", "y", "err", "n", "reason"]
    rows = [
        [p.member, f"{c.timepoint:g}", f"{p.x:g}", p.label,
         f"{p.y:g}", f"{p.err:g}", str(p.n), ""]
        for c in data.curves for p in c.points
    ]
    rows += [[member, "", "", "", "", "", "", reason]
             for member, reason in data.omitted]
    rows += [[member, "", "", "", "", "", "", "value is not a number"]
             for member in data.non_numeric]
    write_csv_rows(ctx, "response_curve", header, rows)
