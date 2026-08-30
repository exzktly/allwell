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
    ResponseData, collect_response, empty_reason, fit_curves, omission_caption,
)
from well_viewer.features.response.renderer import draw_response
from well_viewer.features.response.response_compute import (
    FIT_LABELS, FIT_MODELS, FIT_NONE, ec50_text, spans_enough_decades,
)

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


def _tp_state(ctx) -> dict:
    slice_ = ctx.state.feature_slice("response")
    return slice_.tp_selections if slice_ is not None else {}


def available_timepoints(ctx) -> list[float]:
    """Every timepoint the open dataset holds, ascending."""
    with swallow("read the dataset's timepoints for the response curve"):
        from well_viewer.data.data_loading import get_all_timepoints
        return [float(t) for t in (get_all_timepoints(ctx) or [])]
    return []


def sync_timepoint_menu(ctx) -> None:
    """Seed the picker from the dataset, keeping what the user already chose.

    **Seeded from the shared ``focus_timepoint``, and never writing it.** That
    field is cross-tab; writing it here would move Heat Map and Distribution to
    whatever this tab happened to open on. Seeding with *one* timepoint and not
    all of them is the other half: a twelve-timepoint plate opened as twelve
    overlapping curves per condition is not a plot anyone can read, and a user
    who wants that can ask for it.

    An already-populated selection with everything off is left alone — that is
    a state the user chose, and re-seeding it would override them every redraw.
    """
    slice_ = ctx.state.feature_slice("response")
    if slice_ is None:
        return
    available = {f"{t:g}" for t in available_timepoints(ctx)}
    existing = dict(slice_.tp_selections)
    if existing:
        # Drop timepoints this dataset does not have, keep the rest as they are.
        slice_.tp_selections = {k: v for k, v in existing.items()
                                if k in available}
        if slice_.tp_selections:
            return
    if not available:
        slice_.tp_selections = {}
        return
    focus = getattr(ctx.state.plot, "focus_timepoint", None)
    seed = f"{float(focus):g}" if focus is not None else sorted(available, key=float)[0]
    if seed not in available:
        seed = sorted(available, key=float)[0]
    slice_.tp_selections = {tp: (tp == seed) for tp in sorted(available, key=float)}


def timepoints_for(ctx) -> list[float]:
    """The timepoints to draw, ascending. Empty means the user turned them off."""
    return sorted(float(tp) for tp, on in _tp_state(ctx).items() if on)


def open_timepoint_selector(ctx) -> None:
    """The Bar tab's checkbox menu, over this tab's own selection."""
    from PySide6.QtWidgets import QCheckBox, QMenu, QWidgetAction

    from widgets import menu_exec

    button = _widget(ctx, "_response_tp_button")
    if button is None:
        return
    sync_timepoint_menu(ctx)
    menu = QMenu(button)
    for tp_str in sorted(_tp_state(ctx).keys(), key=float):
        box = QCheckBox(tp_str, menu)
        box.setChecked(bool(_tp_state(ctx).get(tp_str)))

        def _handler(checked: bool, tp=tp_str) -> None:
            _tp_state(ctx)[tp] = bool(checked)
            update_timepoint_button(ctx)
            redraw_response(ctx)

        box.toggled.connect(_handler)
        action = QWidgetAction(menu)
        action.setDefaultWidget(box)
        menu.addAction(action)
    if not menu.actions():
        menu.addAction("No timepoints in this dataset").setEnabled(False)
    menu_exec.exec_menu(menu, button.mapToGlobal(button.rect().bottomLeft()))


def update_timepoint_button(ctx) -> None:
    """The button carries its own readout, as the Bar tab's does."""
    button = _widget(ctx, "_response_tp_button")
    if button is None:
        return
    n = len(timepoints_for(ctx))
    button.setText(f"Timepoints: {n} selected")


def response_data(ctx):
    """The curves for the current X and timepoints.

    Seeds the timepoint selection itself rather than relying on a redraw having
    run first: the CSV export goes through here too, and an export that produced
    no rows because the figure happened not to have been drawn yet would be an
    empty file with no explanation.
    """
    sync_timepoint_menu(ctx)
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
    update_timepoint_button(ctx)

    draw_response(ctx, ax, data,
                  log_x=resolved_log_x(ctx, ax.figure, data),
                  title=_figure_title(ctx, data),
                  ylabel=_y_label(ctx, data),
                  variable_chosen=bool(selected_variable_id(ctx)),
                  timepoints_chosen=bool(timepoints_for(ctx)),
                  fits=response_fits(ctx, data))

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

    An empty figure reports its *reason* rather than its omissions: the reason
    names the fix, and a user looking at nothing needs that more than a list of
    which groups were dropped — which the figure itself is carrying anyway.
    """
    message = _empty_reason(ctx, data) or omission_caption(data)
    if not message:
        return
    with swallow("report what the response curve left out"):
        ctx.notify.set_status(message)


def _empty_reason(ctx, data) -> str:
    """This tab's empty-state sentence for *data*, with the controls' state."""
    return empty_reason(data,
                        variable_chosen=bool(selected_variable_id(ctx)),
                        timepoints_chosen=bool(timepoints_for(ctx)))


def export_response_csv(ctx) -> None:
    """Write the drawn points, **and a row per member that was left out**.

    The omitted rows carry an empty x and a reason, because a CSV of eight
    groups that lists five is the same silent substitution as a figure of eight
    groups that draws five — and a spreadsheet is exactly where someone counts
    rows and believes the total.
    """
    data = response_data(ctx)
    if not data.has_points() and not data.omitted and not data.non_numeric:
        # Refused rather than written empty. A CSV with a header and no rows is
        # indistinguishable from a plate that genuinely had nothing to say, so
        # the refusal carries the figure's own reason — the same sentence, which
        # names the fix rather than the lack.
        with swallow("report that there is nothing to export"):
            ctx.notify.set_status(
                f"Nothing to export. {_empty_reason(ctx, data)}".strip())
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
    rows += _fit_rows(ctx, data)
    write_csv_rows(ctx, "response_curve", header, rows)


def _fit_rows(ctx, data) -> list[list[str]]:
    """One row per fitted curve, in the columns the point rows already use.

    The EC50 goes in ``x`` because that is what it is — a dose on the same
    axis — and its interval and the model go in ``x_label``, so a reader
    sorting the file by x finds the EC50 among the doses it sits between.

    A refused fit still gets a row, carrying its reason. Dropping it would
    leave a spreadsheet whose fits are all successful, which is the same silent
    substitution as a figure that draws five of eight groups.
    """
    rows = []
    for timepoint, fit in sorted(response_fits(ctx, data).items()):
        ec50 = f"{fit.ec50:g}" if fit.ok and fit.ec50 is not None else ""
        rows.append([f"fit ({fit.model})", f"{timepoint:g}", ec50,
                     ec50_text(fit, data.unit), "", "", "",
                     fit.reason])
    return rows


# ── the fit ─────────────────────────────────────────────────────────────────

def fit_settings(ctx) -> tuple[str, float | None, float | None]:
    """``(model, fixed_top, fixed_bottom)`` from this tab's slice."""
    slice_ = ctx.state.feature_slice("response")
    if slice_ is None:
        return FIT_NONE, None, None
    model = str(getattr(slice_, "fit_model", FIT_NONE) or FIT_NONE)
    if model not in FIT_MODELS:
        model = FIT_NONE
    return model, getattr(slice_, "fit_top", None), getattr(slice_, "fit_bottom", None)


def response_fits(ctx, data) -> dict:
    """``{timepoint: FitResult}`` for the figure as the popover has it set."""
    model, top, bottom = fit_settings(ctx)
    return fit_curves(data, model, fixed_top=top, fixed_bottom=bottom)


def set_fit_model(ctx, model: str) -> None:
    slice_ = ctx.state.feature_slice("response")
    if slice_ is None:
        return
    slice_.fit_model = model if model in FIT_MODELS else FIT_NONE
    redraw_response(ctx)


def set_fit_bound(ctx, which: str, text: str) -> None:
    """Pin ``top`` or ``bottom`` to *text*, or free it when *text* is blank.

    A field that cannot be parsed frees the parameter and says so, rather than
    keeping the last good value: a user who typed ``1OO`` and got a curve fitted
    to a top of 100 would have no way to tell that the figure was not answering
    the question they asked.
    """
    slice_ = ctx.state.feature_slice("response")
    if slice_ is None:
        return
    attribute = "fit_top" if which == "top" else "fit_bottom"
    text = (text or "").strip()
    if not text:
        setattr(slice_, attribute, None)
    else:
        try:
            setattr(slice_, attribute, float(text))
        except ValueError:
            setattr(slice_, attribute, None)
            with swallow("report an unreadable fit bound"):
                ctx.notify.set_status(
                    f"{which.title()} is not a number — left free.")
    redraw_response(ctx)


def fit_model_labels() -> list[tuple[str, str]]:
    """``(label, model_id)`` in offer order, for the popover's combo."""
    return [(FIT_LABELS[model], model) for model in FIT_MODELS]


def _y_label(ctx, data=None) -> str:
    """The measurement axis, and the normalisation applied to it.

    The suffix is not decoration. A normalised value on an axis labelled with
    the raw measurement is a number that does not mean what the axis says it
    means, which is the one thing a figure must never do — and this tab shares
    its fold-change state with the Bar and Line tabs, so the axis can become
    normalised without anything on this page being touched.
    """
    channel = str(getattr(ctx.state.channel, "active_channel", "") or "").upper()
    metric = str(getattr(ctx.state.channel, "active_metric", "") or "")
    label = " ".join(part for part in (channel, metric.replace("_", " ")) if part)
    suffix = getattr(data, "normalised", "") if data is not None else ""
    return f"{label} {suffix}".strip() if suffix else label


def _figure_title(ctx, data) -> str:
    """The figure's own title, carrying the Group-by shortfall when there is one.

    **In the title, because a title survives Save.** The shortfall was reaching
    only ``set_status``, which is a transient line the figure does not carry: a
    reader opening the saved PNG a month later had no way to learn that eight
    wells were left out of it. §7's rule is that the shortfall must reach the
    *artifact*.
    """
    from well_viewer.core.group_by_controller import group_by_shortfall

    parts = []
    if data.variable_name:
        parts.append(f"Response vs {data.variable_name}")
    shortfall = group_by_shortfall(ctx)
    if shortfall:
        parts.append(shortfall)
    return " — ".join(parts)


def resolved_log_x(ctx, fig, data) -> bool:
    """Whether to draw x logarithmically: the user's choice, else the data's.

    ``x_log`` is the existing Properties toggle, stored per figure — so a
    reader can tell "the user turned it off" from "the user never touched it",
    and only the second is the tab's to decide. **Unset asks the data**: on once
    the positive doses span two decades, which is the point at which a linear
    axis puts three quarters of the points in its leftmost tenth. Defaulting to
    linear instead would mean every dose series opens unreadable and every user
    fixes it by hand before they can see anything.

    Set either way, the user's answer stands. A tab that overrode a toggle from
    the data on every redraw would be a control that does not stay where it is
    put.
    """
    from well_viewer.core.plot_style import figure_pref_is_set, figure_prefs

    with swallow("read this figure's x_log preference"):
        if figure_pref_is_set(ctx, fig, "x_log"):
            return bool(figure_prefs(ctx, fig).get("x_log"))
    return spans_enough_decades(
        [p.x for c in data.curves for p in c.points if p.x > 0])
