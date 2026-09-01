"""Response Curves orchestration — fill the X combo, redraw, export.

Every rule about what a curve contains is in ``collect``; every rule about the
axes is in ``response_compute`` and ``renderer``. This is the wiring, and it is
kept thin on purpose: a rule that lives in a signal handler is a rule nothing
tests.
"""

from __future__ import annotations

import logging

from well_viewer.core.channel_state_controller import col_for_scatter_axis
from well_viewer.core.csv_export import write_csv_rows
from well_viewer.core.safe import swallow
from well_viewer.domain.response_colors import response_color_mode
from well_viewer.domain.sample_variables import NAME_JOIN, NUMBER
from well_viewer.features.response.collect import (
    Y_FRACTION, Y_MEAN, ResponseData, collect_response, empty_reason, fit_curves,
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


#: What the Series combo says when no split is wanted. Kept first in the list so
#: the tab opens on the behaviour it has always had, and what the menu's
#: clear-everything action is called.
NO_SERIES_LABEL = "None"


def series_variables(ctx) -> list:
    """Every variable that could split the curves — Text *and* Number.

    Text and Number both, unlike the X combo: a plate varying dose against
    *seeding density* has two numeric variables and either could reasonably be
    the axis while the other names the curves. The X variable itself is
    excluded by :func:`refresh_series_control`, since a series on the axis
    variable draws one curve per dose with a single point on each.
    """
    vset = getattr(ctx.state.selection, "variables", None)
    return list(getattr(vset, "variables", ()) or ())


def selected_series_ids(ctx) -> list[str]:
    """The chosen series variables, in the order the user picked them.

    Read from the slice rather than from the control, because the control is a
    menu that is rebuilt every time it opens and the order is the user's: a
    figure split by cell line and then by RNA labels its curves
    ``HeLa · shGFP``, and reversing that pair would relabel every curve on a
    redraw.
    """
    slice_ = ctx.state.feature_slice("response")
    ids = list(getattr(slice_, "series_variable_ids", ()) or []) if slice_ else []
    on_x = selected_variable_id(ctx)
    offered = {v.id for v in series_variables(ctx)}
    return [vid for vid in ids if vid in offered and vid != on_x]


def toggle_series_variable(ctx, variable_id: str, on: bool) -> None:
    """Add or remove one series variable, keeping the rest in their order.

    Appended rather than inserted, so the order is the order they were chosen —
    which is the order the curve labels read in, and the order the legend sorts
    by. Removing and re-adding one moves it to the end, which is the only
    reordering control this needs to be worth building.
    """
    slice_ = ctx.state.feature_slice("response")
    if slice_ is None or not variable_id:
        return
    ids = [vid for vid in (slice_.series_variable_ids or []) if vid != variable_id]
    if on:
        ids.append(variable_id)
    slice_.series_variable_ids = ids


def series_button_text(ctx) -> str:
    """What the Series button says: the variables it is splitting by.

    Their names rather than a count, because the reader needs to know *which*
    two a figure of four curves is crossing, and the button is the only place
    on screen that says so once the menu is closed.
    """
    chosen = set(selected_series_ids(ctx))
    names = [v.name for v in series_variables(ctx) if v.id in chosen]
    # In the user's own pick order, not the variable list's.
    order = {vid: i for i, vid in enumerate(selected_series_ids(ctx))}
    by_id = {v.id: v.name for v in series_variables(ctx)}
    names = [by_id[vid] for vid in sorted(order, key=order.get) if vid in by_id]
    return f"Series: {NAME_JOIN.join(names)}" if names else f"Series: {NO_SERIES_LABEL}"


def refresh_series_control(ctx) -> None:
    """Drop any series variable that is no longer offered, and repaint the button.

    Rebuilt on every redraw because the X combo can move under it: picking
    ``Dose`` as the axis while ``Dose`` was a series has to drop it rather than
    draw one curve per dose. A pick survives whenever it still makes sense, the
    same rule ``refresh_x_combo`` follows.
    """
    slice_ = ctx.state.feature_slice("response")
    if slice_ is not None:
        kept = selected_series_ids(ctx)
        if kept != list(slice_.series_variable_ids or []):
            slice_.series_variable_ids = kept
    button = _widget(ctx, "_response_series_btn")
    if button is not None:
        button.setText(series_button_text(ctx))


def open_series_selector(ctx) -> None:
    """The checkbox menu behind the Series button.

    A menu rather than a second combo: the number of variables a plate can be
    split by is the number of variables it has, and a control that offers two
    of them is the single-select combo this replaced wearing a wider coat.
    """
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QCheckBox, QMenu, QWidgetAction

    from widgets import menu_exec

    button = _widget(ctx, "_response_series_btn")
    if button is None:
        return
    on_x = selected_variable_id(ctx)
    chosen = set(selected_series_ids(ctx))
    menu = QMenu(button)
    offered = [v for v in series_variables(ctx) if v.id != on_x]
    for variable in offered:
        label = (f"{variable.name} ({variable.unit})" if variable.unit
                 else variable.name)
        box = QCheckBox(label, menu)
        box.setChecked(variable.id in chosen)

        def _handler(checked: bool, vid=variable.id) -> None:
            toggle_series_variable(ctx, vid, checked)
            refresh_series_control(ctx)
            redraw_response(ctx)

        box.toggled.connect(_handler)
        action = QWidgetAction(menu)
        action.setDefaultWidget(box)
        menu.addAction(action)
    if not offered:
        menu.addAction("No other variable to split by").setEnabled(False)
    else:
        menu.addSeparator()

        def _clear() -> None:
            slice_ = ctx.state.feature_slice("response")
            if slice_ is not None:
                slice_.series_variable_ids = []
            refresh_series_control(ctx)
            redraw_response(ctx)

        action = QAction(NO_SERIES_LABEL, menu)
        action.triggered.connect(lambda _=False: _clear())
        menu.addAction(action)
    menu_exec.exec_menu(menu, button.mapToGlobal(button.rect().bottomLeft()))


#: The Property label that is not a column. Named once here rather than
#: spelled at each comparison: it is the same string ``populate_metric_combo``
#: appends and the aggregate Scatter tab keys on, and a second spelling of it
#: is how one of the two sites silently stops matching.
FRACTION_LABEL = "Fraction above threshold"


def response_channel_label(ctx) -> str:
    """The channel this tab measures, as the combo's **label**.

    Its own, falling back to the shared channel's label — ``active_channel_label``
    rather than the raw ``active_channel``, because the two differ for every
    entry kind: a real channel's token is ``gfp`` where the combo says ``GFP``,
    and a ratio's is ``ratio:GFP/MCHERRY`` where the combo says ``GFP/MCHERRY``.
    Comparing the token against the combo with a case-sensitive ``findText``
    left the box reading ``GFP`` while the tab drew ``MCHERRY``.

    A pinned channel the current dataset does not offer is dropped rather than
    kept: it names a column no frame has, and the combo would show the fallback
    while the figure asked for the pin.
    """
    from well_viewer.core.channel_state_controller import active_channel_label

    slice_ = ctx.state.feature_slice("response")
    own = str(getattr(slice_, "channel", "") or "") if slice_ else ""
    if own and own in _offered_channel_labels(ctx):
        return own
    with swallow("read the shared channel's label for the response tab"):
        return str(active_channel_label(ctx) or "")
    return ""


def _offered_channel_labels(ctx) -> set[str]:
    """The labels the Channel combo currently holds."""
    combo = _widget(ctx, "_response_ch_cb")
    if combo is None:
        return set()
    return {combo.itemText(i) for i in range(combo.count())}


#: Back-compat alias. ``response_channel`` returned a label and is still read
#: by the tab builder and the tests under that name.
def response_channel(ctx) -> str:
    return response_channel_label(ctx)


def response_metric_label(ctx) -> str:
    """The Property label this tab draws, falling back to the shared metric."""
    from well_viewer.data.metric_labels import METRIC_KEY_TO_LABEL

    slice_ = ctx.state.feature_slice("response")
    own = str(getattr(slice_, "metric_label", "") or "") if slice_ else ""
    if own:
        return own
    shared = str(getattr(ctx.state.channel, "active_metric", "") or "")
    return METRIC_KEY_TO_LABEL.get(shared, "Mean Intensity")


def response_draws_fraction(ctx) -> bool:
    return response_metric_label(ctx) == FRACTION_LABEL


def response_val_col(ctx) -> str:
    """The column the stats producers should read for this tab.

    **Through the aggregate Scatter tab's own resolver, not a second one.**
    Composing ``f"{channel}_{metric}"`` here was wrong for three of the entry
    kinds the Channel combo offers, and wrong on the *untouched* path where the
    tab simply follows the band: a ratio's active channel is ``GFP/MCHERRY``
    and its column is ``ratio:GFP/MCHERRY``, a shape feature's are both
    ``shape_area``, and pasting a metric onto either produced a column no frame
    has — so ``resolve_value_series`` returned a NaN Series and the figure drew
    nothing, with the empty state naming a cause that was not the cause.

    ``col_for_scatter_axis`` already branches on ``is_ratio_key`` and
    ``is_shape_column`` through ``label_to_channel_key``, lowercases the token,
    and maps ``fraction_above_threshold`` to the channel's mean-intensity
    column — which is exactly this tab's rule, because the fraction is the
    share of cells whose mean intensity clears the ON cutoff. One definition,
    reached from both tabs.
    """
    from well_viewer.data.metric_labels import METRIC_LABEL_TO_KEY

    channel = response_channel_label(ctx)
    if not channel:
        return str(getattr(ctx.state.channel, "active_val_col", "") or "")
    label = response_metric_label(ctx)
    key = ("fraction_above_threshold" if label == FRACTION_LABEL
           else METRIC_LABEL_TO_KEY.get(label, "mean_intensity"))
    return col_for_scatter_axis(ctx, channel, key)


def response_channel_key(ctx) -> str:
    """The tab's channel as a **key** — the lowercase token or ``ratio:…``.

    The combo holds display labels; every other channel consumer stores the
    key, converting through ``channel_key_for_label`` at the point of choice.
    This tab stored the raw label and composed a column from it, which survived
    for real channels only on ``resolve_value_series``'s case-fold fallback —
    a shim its own comment describes as legacy — and not at all for ratios.
    """
    from well_viewer.core.channel_state_controller import channel_key_for_label

    label = response_channel_label(ctx)
    return channel_key_for_label(ctx, label) if label else ""


def on_response_channel_changed(ctx) -> None:
    """Store the tab's own channel, then reshape its Property list for it.

    Its own, and deliberately **not** written back to
    ``ctx.state.channel.active_channel``: this control moving Bar and Line is
    the surprise a tab-local control exists to avoid, and it is how the
    aggregate Scatter tab's pair already behaves.
    """
    combo = _widget(ctx, "_response_ch_cb")
    slice_ = ctx.state.feature_slice("response")
    if combo is None or slice_ is None:
        return
    slice_.channel = str(combo.currentText() or "")
    refresh_response_metric_combo(ctx)
    redraw_response(ctx)


def on_response_metric_changed(ctx) -> None:
    combo = _widget(ctx, "_response_metric_cb")
    slice_ = ctx.state.feature_slice("response")
    if combo is None or slice_ is None:
        return
    slice_.metric_label = str(combo.currentText() or "")
    redraw_response(ctx)


def refresh_response_metric_combo(ctx) -> None:
    """Reshape the Property list for the chosen channel, fraction included.

    Through the shared ``populate_metric_combo`` with ``include_fraction=True``
    — the same call the aggregate Scatter tab makes — so the ratio and shape
    collapses, the smFISH gating and the fallback all behave identically here
    and this tab holds no second copy of those rules.
    """
    from well_viewer.core.channel_state_controller import populate_metric_combo

    combo = _widget(ctx, "_response_metric_cb")
    if combo is None:
        return
    channel = response_channel(ctx)
    populate_metric_combo(ctx, combo, channel_entry=channel,
                          smfish_channel=channel, include_fraction=True)


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
    _store_x_variable(ctx)


def _store_x_variable(ctx) -> None:
    """Mirror the X combo's pick onto the slice.

    ``domain/plot_members`` needs the axis variable — under a *Group by* this
    tab pools into the cross of the axis with what splits its curves — and it
    cannot read a widget. Written here as well as in the change handler because
    this function decides the selection too: it re-lists the variables on every
    redraw and falls back to the first when the user's pick has gone, with
    signals blocked, so the handler does not fire for the fall-back and the
    slice would keep naming a variable the combo no longer shows.
    """
    slice_ = ctx.state.feature_slice("response")
    if slice_ is not None:
        slice_.x_variable_id = selected_variable_id(ctx)


def _tp_state(ctx) -> dict:
    slice_ = ctx.state.feature_slice("response")
    return slice_.tp_selections if slice_ is not None else {}


def available_timepoints(ctx) -> list[float]:
    """Every timepoint the open dataset holds, ascending.

    Through ``timepoint_sync.plottable_timepoints`` — the same answer the Bar
    tab's picker takes. This asked ``data_loading.get_all_timepoints`` instead,
    which reads only the numeric ``timepoint_hours`` column: on a plate whose
    filename schema has no timepoint slot that column is NaN in every row, so
    the picker offered nothing, the button read "Timepoints: 0 selected" and the
    figure was empty — while every other tab listed the plate's timepoints and
    drew it. The aggregator files those rows at 0.0, which is what the shared
    helper returns, so asking for it draws them.

    It is also read on every redraw, and the old route walked every loaded
    well's rows to answer — a gate mask and a numeric coercion per well, per
    repaint, to recompute something the dataset already caches.
    """
    with swallow("read the dataset's timepoints for the response curve"):
        from well_viewer.core.timepoint_sync import plottable_timepoints
        return [float(t) for t in plottable_timepoints(ctx)]
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

    **Reconciled in both directions.** This used to drop the timepoints the
    dataset lacks and stop, which meant a stored selection could only ever
    shrink: a tab whose selection was saved while the picker offered one
    timepoint went on offering that one after the dataset gained the rest,
    because the saved dict was non-empty and was taken as the whole answer. It
    is a real state and not a hypothetical — the release before this one could
    not see a plate's timepoints at all, so every selection saved under it says
    ``{"0": True}`` — and it is invisible from the tab, which reports what it
    holds rather than what the plate has. The user's on/off answers are kept
    for the timepoints they were made about; timepoints they have never been
    asked about are added **off**, so a plate that gains a timepoint offers it
    without silently drawing it.
    """
    slice_ = ctx.state.feature_slice("response")
    if slice_ is None:
        return
    available = [f"{t:g}" for t in available_timepoints(ctx)]
    existing = dict(slice_.tp_selections)
    if existing and any(tp in existing for tp in available):
        # The stored selection is about *this* plate: keep every answer the
        # user gave, including all-off, and offer the timepoints they were
        # never asked about — off, so a plate that gains one does not silently
        # start drawing it.
        slice_.tp_selections = {tp: bool(existing.get(tp, False))
                                for tp in available}
        return
    # Either nothing is stored, or what is stored names a plate this is not —
    # a different dataset opened into the same session. Seed afresh rather than
    # leaving a picker whose entries belong to something else.
    if not available:
        slice_.tp_selections = {}
        return
    available = set(available)
    focus = getattr(ctx.state.plot, "focus_timepoint", None)
    seed = f"{float(focus):g}" if focus is not None else sorted(available, key=float)[0]
    if seed not in available:
        seed = sorted(available, key=float)[0]
    slice_.tp_selections = {tp: (tp == seed) for tp in sorted(available, key=float)}


def timepoints_for(ctx) -> list[float]:
    """The timepoints to draw, ascending. Empty means the user turned them off."""
    return sorted(float(tp) for tp, on in _tp_state(ctx).items() if on)


def open_timepoint_selector(ctx) -> None:
    """The Bar tab's checkbox menu, over this tab's own selection.

    With its All / None pair too. A twelve-timepoint plate is twelve clicks to
    see the whole series and twelve more to get back, and the tab a click away
    offers both — a picker that is the same control minus two actions reads as
    the one that is broken.
    """
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QCheckBox, QMenu, QWidgetAction

    from widgets import menu_exec

    button = _widget(ctx, "_response_tp_button")
    if button is None:
        return
    sync_timepoint_menu(ctx)
    menu = QMenu(button)
    boxes: dict[str, QCheckBox] = {}
    for tp_str in sorted(_tp_state(ctx).keys(), key=float):
        box = QCheckBox(tp_str, menu)
        box.setChecked(bool(_tp_state(ctx).get(tp_str)))
        boxes[tp_str] = box

        def _handler(checked: bool, tp=tp_str) -> None:
            _tp_state(ctx)[tp] = bool(checked)
            update_timepoint_button(ctx)
            redraw_response(ctx)

        box.toggled.connect(_handler)
        action = QWidgetAction(menu)
        action.setDefaultWidget(box)
        menu.addAction(action)
    if not boxes:
        menu.addAction("No timepoints in this dataset").setEnabled(False)
    else:
        menu.addSeparator()

        def _set_all(value: bool) -> None:
            # The checkboxes are set with their signals blocked and the state
            # written directly, so twelve toggles do not queue twelve redraws
            # of the same figure. One repaint at the end, from the state the
            # user asked for.
            for tp_str, box in boxes.items():
                blocked = box.blockSignals(True)
                try:
                    box.setChecked(value)
                finally:
                    box.blockSignals(blocked)
                _tp_state(ctx)[tp_str] = value
            update_timepoint_button(ctx)
            redraw_response(ctx)

        for label, value in (("All", True), ("None", False)):
            action = QAction(label, menu)
            action.triggered.connect(lambda _=False, v=value: _set_all(v))
            menu.addAction(action)
    menu_exec.exec_menu(menu, button.mapToGlobal(button.rect().bottomLeft()))


def update_timepoint_button(ctx) -> None:
    """The button carries its own readout, as the Bar tab's does.

    Through the shared ``multiselect_button_text``, so it reads "2/5 selected"
    rather than "2 selected". The count alone cannot say whether the other
    three are unticked or simply not there, and this tab's failure mode was
    exactly that: a picker with nothing in it, reading "0 selected", which is
    also what a user who unticked everything sees.
    """
    from well_viewer.core.ui_helpers import multiselect_button_text

    button = _widget(ctx, "_response_tp_button")
    if button is None:
        return
    button.setText(multiselect_button_text("Timepoints", _tp_state(ctx)))


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
    # Both layers, from the seam that owns which is which: *Group by* splits
    # the curves outermost and the tab's own *Series* inside it. Passing the
    # Series combo alone here is what made the plate-wide grouping do nothing
    # on this tab but empty it — the members were pooled across the axis and
    # every one of them rolled up to "wells disagree".
    from well_viewer.domain.plot_members import response_split_variables

    return collect_response(
        ctx, variable_id, timepoints_for(ctx), response_split_variables(ctx),
        Y_FRACTION if response_draws_fraction(ctx) else Y_MEAN,
        val_col=response_val_col(ctx))


def redraw_response(ctx) -> None:
    """Repaint the figure. Safe before the tab is built.

    **The shortfall is not pushed to the status line.** ``notify.set_status``
    writes the *persistent* status bar, and a redraw that succeeds has no reason
    to write anything — so the sentence outlived its condition: fill in the
    missing dose and the figure's caption clears while the bar goes on saying
    the dose is missing, contradicting the plot above it with no redraw able to
    correct it. It is the same defect the bar tab's significance note was moved
    off the status line for. The caption and the empty state carry it instead,
    and they cannot go stale: the redraw that resolves the condition is the
    redraw that clears them. The status line is still used for the two things
    it is right for — reporting an action the user just took, where there is no
    figure to write on.
    """
    ax = _widget(ctx, "_response_ax")
    canvas = _widget(ctx, "_response_canvas")
    if ax is None or canvas is None:
        return
    refresh_x_combo(ctx)
    refresh_series_control(ctx)
    data = response_data(ctx)
    update_timepoint_button(ctx)
    log_x = resolved_log_x(ctx, ax.figure, data)
    if log_x and not _log_x_was_asked_for(ctx, ax.figure):
        data.scale_notice = (
            f"{data.variable_name or 'x'} drawn logarithmic from the values "
            f"in use — set it in Properties to override")

    draw_response(ctx, ax, data,
                  log_x=log_x,
                  log_base=resolved_log_base(ctx, ax.figure),
                  color_mode=response_color_mode(ctx.state.plot),
                  title=_figure_title(ctx, data),
                  ylabel=_y_label(ctx, data),
                  variable_chosen=bool(selected_variable_id(ctx)),
                  timepoints_chosen=bool(timepoints_for(ctx)),
                  fits=response_fits(ctx, data))

    from well_viewer.core.plot_style import apply_export_style_to_current
    apply_export_style_to_current(ctx, ax.figure, canvas)
    canvas.draw_idle()


def on_x_variable_changed(ctx) -> None:
    _store_x_variable(ctx)
    redraw_response(ctx)


def _empty_reason(ctx, data) -> str:
    """This tab's empty-state sentence for *data*, with the controls' state."""
    return empty_reason(data,
                        variable_chosen=bool(selected_variable_id(ctx)),
                        timepoints_chosen=bool(timepoints_for(ctx)))


def _blank_if_undefined(value) -> str:
    """A number, or an empty cell for one that was never computed.

    ``err`` is deliberately NaN for a spread that could not be defined — one
    replicate has no spread, and §7's rule is that undefined must not be written
    as zero. ``f"{nan:g}"`` renders the literal ``nan``, which is a third token
    for the same idea: the rest of the app's exports blank the cell
    (``features/export/service._num``), and two guards already pin that —
    ``test_events_csv_columns`` and ``test_fov_label_integrity``. One exporter
    with its own spelling is how a reader ends up believing the two mean
    different things.
    """
    return "" if value is None or value != value else f"{value:g}"


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
    # A ``series`` column even when no series is chosen, where it is simply
    # blank. A column that appears and disappears with a control makes two
    # exports of the same plate un-diffable and breaks any script reading it by
    # position, and a blank cell already means "this figure had no series".
    # ``spread_unit`` beside ``n`` because they count different things once
    # Spread: FOV is on: ``n`` is the member's loaded wells, and the error is
    # taken across pooled FOVs. Without the unit stated, a reader reconstructing
    # SD as ``SEM x sqrt(n)`` gets a number from two columns that do not belong
    # to the same denominator — a wrong value produced from a correct file.
    header = ["member", "series", "timepoint", "x", "x_label", "y", "err", "n",
              "spread_unit", "reason"]
    unit = data.replicate_unit
    rows = [
        [p.member, c.series_label, f"{c.timepoint:g}", f"{p.x:g}", p.label,
         f"{p.y:g}", _blank_if_undefined(p.err), str(p.n), unit, ""]
        for c in data.curves for p in c.points
    ]
    rows += [[member, "", "", "", "", "", "", "", "", reason]
             for member, reason in data.omitted]
    rows += [[member, "", "", "", "", "", "", "", "", "value is not a number"]
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
    labels = {c.key: c.series_label for c in data.curves}
    rows = []
    for key, fit in sorted(response_fits(ctx, data).items()):
        timepoint, series_id = key
        ec50 = f"{fit.ec50:g}" if fit.ok and fit.ec50 is not None else ""
        # The series goes in its own column, the same one the point rows use,
        # because a file whose fit rows all read ``fit (4pl)`` cannot say which
        # curve each EC50 came from — and with two series that is two numbers a
        # reader has no way to tell apart.
        series = labels.get(key) or ""
        rows.append([f"fit ({fit.model})", series, f"{timepoint:g}", ec50,
                     ec50_text(fit, data.unit), "", "", "", "",
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
    channel = response_channel(ctx).upper()
    if getattr(data, "y_metric", Y_MEAN) == Y_FRACTION:
        # Not the channel's Property. The fraction is a different quantity from
        # whatever the Property names, computed against the ON cutoff, and an
        # axis reading "GFP mean intensity" over a curve of fractions is the one
        # thing a figure must never do.
        label = " ".join(part for part in (channel, "fraction above threshold")
                         if part)
    else:
        label = " ".join(part for part in (channel,
                                           response_metric_label(ctx).lower())
                         if part)
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
    for shortfall in (group_by_shortfall(ctx), _uncrossed_grouping(ctx)):
        if shortfall:
            parts.append(shortfall)
    return " — ".join(parts)


def _uncrossed_grouping(ctx) -> str:
    """Said when a grouping is chosen and this tab could not use it.

    Under *Group by* this tab pools into the cross of the grouping with the
    axis variable, and a cross can come out empty — the two are filled on
    disjoint parts of the plate, so no well holds both. The members then fall
    back to the user's own groups rather than the figure emptying, which is the
    right answer and a **substitution**: the band reads "Group by: Cell line"
    over curves that are not grouped by cell line at all, and nothing else on
    the page would say so. ``group_by_shortfall`` cannot: it asks whether the
    grouping matched any wells, and here it did — it is the *crossing* that
    found none.
    """
    from well_viewer.domain.plot_members import crossed_response_sets

    variable_id = str(getattr(ctx.state.plot, "group_by_variable", "") or "")
    if not variable_id or not selected_variable_id(ctx):
        return ""
    if crossed_response_sets(ctx):
        return ""
    vset = getattr(ctx.state.selection, "variables", None)
    variable = vset.by_id(variable_id) if vset is not None else None
    if variable is None:
        return ""
    axis = vset.by_id(selected_variable_id(ctx))
    axis_name = axis.name if axis is not None else "the x variable"
    return (f"No well holds both {variable.name} and {axis_name}, so these "
            f"curves are your own groups rather than {variable.name}")


def _log_x_was_asked_for(ctx, fig) -> bool:
    """Whether the user has an ``x_log`` answer of their own for this figure."""
    from well_viewer.core.plot_style import figure_pref_is_set

    with swallow("read whether this figure's x_log preference is set"):
        return figure_pref_is_set(ctx, fig, "x_log")
    return False


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


def resolved_log_base(ctx, fig) -> float:
    """The base this figure's log x axis is drawn in — the Properties value.

    Unlike ``x_log`` there is nothing to derive from the data: a base is a way
    of reading an axis, not a property of the doses, so an unset preference is
    simply base 10. The renderer needs it in hand because it positions the
    zero-dose column one step of this base below the lowest real dose, and
    because setting the scale itself is what records the base on the axis for
    the restyle that follows.
    """
    from well_viewer.core.plot_style import (
        DEFAULT_LOG_BASE, figure_prefs, log_base_value,
    )

    with swallow("read this figure's x log base"):
        return log_base_value(figure_prefs(ctx, fig).get("x_log_base", "10"))
    return DEFAULT_LOG_BASE
