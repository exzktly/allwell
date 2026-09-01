"""The Response Curves tab, driven rather than inspected.

``test_response_collect.py`` covers what a curve is made of. This covers the
half that has shipped broken twice in this repository: that the controls are
connected, that the redraw reaches the figure, and that the tab says what it
cannot draw.

Nothing here asserts a control exists. Each one is pressed and the result read
back — from the axes where the figure is what matters, and from the exporter's
rows where the file is.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from well_viewer.domain import sample_variables as sv
from well_viewer.features.response import controller as rctl


class _Registry:
    def __init__(self, **widgets):
        self._d = dict(widgets)

    def register(self, name, widget):
        self._d[name] = widget
        return widget

    def get_or_none(self, name):
        return self._d.get(name)


def _variable(kind=sv.NUMBER, *, name="Dose", unit="µM", pairs=(("1", 1.0),)):
    return sv.Variable(id=name.lower(), name=name, kind=kind, unit=unit,
                       values=tuple(sv.VariableValue(f"v{i}", lab, num)
                                    for i, (lab, num) in enumerate(pairs)))


@pytest.fixture
def tab(qtbot, monkeypatch):
    """A ctx with a real X combo and a real axes, and stubbed statistics."""
    from matplotlib.figure import Figure

    import well_viewer.core.channel_state_controller as csc
    import well_viewer.core.plot_style as ps
    import well_viewer.domain.stats_compute as sc
    from widgets.labeled_combo import LabeledComboBox

    monkeypatch.setattr(csc, "on_cutoff_for_column", lambda *a, **k: 0.0)
    monkeypatch.setattr(sc, "compute_rep_stats",
                        lambda ctx, rset, t, thr, sem, **kw: (10.0, 1.0, 0.5, 0.1))
    monkeypatch.setattr(ps, "apply_ax_style", lambda *a, **k: None)
    monkeypatch.setattr(ps, "apply_export_style_to_current", lambda *a, **k: None)

    from PySide6.QtWidgets import QPushButton

    combo = LabeledComboBox("X")
    qtbot.addWidget(combo)
    # Connected the way ``build_response_tab`` connects it. An unconnected combo
    # makes every test that changes X a test of ``refresh_x_combo`` alone: the
    # signal that carries the change to the figure is exactly the part this
    # repository has shipped dead, and a fixture that leaves it out cannot see
    # it. It is also what made the restore's ``blockSignals`` untestable — the
    # mutation that removed it left the whole file green.
    combo.currentIndexChanged.connect(
        lambda *_: rctl.on_x_variable_changed(ctx))
    # Connected the way ``build_response_tab`` connects it — an unconnected
    # combo makes every series test a test of ``refresh_series_combo`` alone,
    # which is the exact gap that let the X combo ship dead once already.
    series_combo = LabeledComboBox("Series")
    qtbot.addWidget(series_combo)
    series_combo.currentIndexChanged.connect(
        lambda *_: rctl.on_series_changed(ctx))
    # This tab's own Channel and Property, connected the way the builder
    # connects them. Populated with plain labels here rather than through
    # ``populate_metric_combo``, which needs the whole ChannelState; the tests
    # that care about the real list drive that function directly.
    ch_combo = LabeledComboBox("Channel")
    qtbot.addWidget(ch_combo)
    ch_combo.addItems(["GFP", "RFP"])
    ch_combo.currentIndexChanged.connect(
        lambda *_: rctl.on_response_channel_changed(ctx))
    y_combo = LabeledComboBox("Property")
    qtbot.addWidget(y_combo)
    y_combo.addItems(["Mean Intensity", rctl.FRACTION_LABEL])
    y_combo.currentIndexChanged.connect(
        lambda *_: rctl.on_response_metric_changed(ctx))
    tp_button = QPushButton("Timepoints: 0 selected")
    qtbot.addWidget(tp_button)
    # A bare ``Figure``, not ``pyplot.subplots``: pyplot would build a Qt canvas
    # and a navigation toolbar this test never shows, and tearing those down
    # after the fixture raises "Internal C++ object already deleted". The axes
    # is all the renderer touches.
    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    canvas = SimpleNamespace(draw_idle=lambda: None)

    # The tab's own timepoint selection lives on a feature slice, reached the
    # way core reaches one — a stub must answer ``feature_slice`` or the picker
    # silently has nothing to select.
    from well_viewer.core.cell_gating_state import CellGatingState
    from well_viewer.core.export_prefs_state import ExportPrefsState
    from well_viewer.features.response.state import ResponseState

    slices = {"response": ResponseState()}
    statuses = []
    ctx = SimpleNamespace(
        # ``_response_fig`` too: ``figure_pref_key`` looks the figure up by its
        # registry name, and without it the prefs get an unstable key and warn.
        widgets=_Registry(_response_x_cb=combo, _response_ax=ax,
                          _response_fig=fig, _response_canvas=canvas,
                          _response_tp_button=tp_button,
                          _response_series_cb=series_combo,
                          _response_metric_cb=y_combo,
                          _response_ch_cb=ch_combo),
        notify=SimpleNamespace(set_status=statuses.append),
        state=SimpleNamespace(
            dataset=SimpleNamespace(well_paths={}),
            selection=SimpleNamespace(selections=[], selected_wells=set(),
                                      variables=sv.VariableSet()),
            # The fold-change fields are the real ``PlotState``'s, spelled out
            # rather than omitted: this tab normalises through the shared path,
            # and a stub missing them would make the collector raise on a plate
            # that works perfectly in the app.
            # ``test_response_stub_matches_plot_state`` is what keeps this list
            # honest as the real state grows.
            plot=SimpleNamespace(group_by_variable="", bar_pool_replicates=True,
                                 use_sem=True, focus_timepoint=24.0,
                                 line_order_rsets=[], line_order_wells=[],
                                 fc_vs_control_on=False, fc_control=None,
                                 fc_vs_t0_on=False, fc_control_mode="single",
                                 fc_control_map={}, fc_control_render="self",
                                 fc_baseline_tp="", fc_op_ctrl="ratio",
                                 fc_op_t0="ratio", fc_order="ctrl_first"),
            # ``active_metric`` spelled out: the real ``ChannelState`` always
            # carries it and ``_y_label`` reads it, so a stub that omitted it
            # made the axis read "GFP" and any label assertion agree with a
            # tab that had dropped the metric entirely.
            # ``label_to_channel_key`` and ``smfish_channels`` spelled out
            # because ``populate_metric_combo`` reads both, and this tab now
            # calls it for its own Property list. A stub missing them made the
            # refresh raise on a plate that works perfectly in the app —
            # ``test_response_stub_matches_plot_state``'s lesson, one slice
            # over.
            channel=SimpleNamespace(active_val_col="gfp_mean_intensity",
                                    active_channel="gfp",
                                    active_metric="mean_intensity",
                                    # A real label -> key map, because the
                                    # column resolver goes through it to tell a
                                    # ratio and a shape feature from a plain
                                    # channel. Empty, it could not, and the
                                    # tests below passed against a resolver
                                    # that pasted the display label into the
                                    # column name.
                                    label_to_channel_key={"GFP": "gfp",
                                                          "RFP": "rfp"},
                                    ratio_metrics=(),
                                    smfish_channels=(),
                                    fluor_channels=("gfp", "rfp")),
            feature_slice=slices.get,
            # The real slice, not a stub: ``figure_prefs`` seeds it lazily and
            # distinguishes "never set" from "set to off", which is the whole
            # property the log-x tests are about.
            export_prefs=ExportPrefsState(),
            # ``active_channel_label`` reaches the gating slice through
            # ``fluor_gate_for``; the real ``AppState`` always carries it, and a
            # stub without it made the label lookup raise on a path the app
            # takes on every redraw.
            cell_gating=CellGatingState(),
        ),
        runtime=SimpleNamespace(barplot=SimpleNamespace(current_keys=lambda: [])),
    )
    return SimpleNamespace(ctx=ctx, combo=combo, ax=ax, statuses=statuses,
                           series=series_combo, y=y_combo,
                           channel=ch_combo,
                           monkeypatch=monkeypatch,
                           w=ctx.widgets.get_or_none)


def _load(tab, variable, assignments, groups, timepoints=(24.0,)):
    """Point the ctx at a dataset, and say which timepoints it holds.

    Written into ``dataset.all_timepoints_cache``, which is the field the app
    fills on load and the field every tab's picker reads. It used to be a stub
    over ``data_loading.get_all_timepoints`` — the function this tab alone
    called — and a stub of a function nothing else uses cannot notice that the
    rest of the app is answering the question somewhere else.
    """
    tab.ctx.state.dataset.all_timepoints_cache = [float(t) for t in timepoints]
    wells = sorted({w for ws in (g[1] for g in groups) for w in ws})
    tab.ctx.state.dataset.well_paths = {w: f"/{w}.csv" for w in wells}
    tab.ctx.state.selection.variables = sv.VariableSet(
        variables=(variable,), assignments={variable.id: assignments})
    tab.ctx.state.selection.selections = [
        {"id": f"g{i}", "name": n, "wells": w} for i, (n, w) in enumerate(groups)]
    tab.ctx.runtime.barplot.current_keys = lambda: [g[0] for g in groups]


def _load_two_series(tab, timepoints=(24.0,)):
    """A dose series run on two cell lines — the plate the feature is for."""
    tab.ctx.state.dataset.all_timepoints_cache = [float(t) for t in timepoints]
    dose = _variable(pairs=(("1", 1.0), ("10", 10.0)))
    line = sv.Variable(
        id="cl", name="Cell line", kind=sv.TEXT, unit="",
        values=(sv.VariableValue("hela", "HeLa", None),
                sv.VariableValue("u2os", "U2OS", None)))
    wells = ["A01", "A02", "B01", "B02"]
    tab.ctx.state.dataset.well_paths = {w: f"/{w}.csv" for w in wells}
    tab.ctx.state.selection.variables = sv.VariableSet(
        variables=(dose, line),
        assignments={"dose": {"A01": "v0", "A02": "v1",
                              "B01": "v0", "B02": "v1"},
                     "cl": {"A01": "hela", "A02": "hela",
                            "B01": "u2os", "B02": "u2os"}})
    groups = [("H-lo", ["A01"]), ("H-hi", ["A02"]),
              ("U-lo", ["B01"]), ("U-hi", ["B02"])]
    tab.ctx.state.selection.selections = [
        {"id": f"g{i}", "name": n, "wells": w} for i, (n, w) in enumerate(groups)]
    tab.ctx.runtime.barplot.current_keys = lambda: [g[0] for g in groups]


def _pick_series(tab, variable_id):
    """Redraw so the combo is populated, then choose *variable_id* in it.

    The order matters and is the app's own: ``refresh_series_combo`` runs
    inside the redraw, so a test that calls ``findData`` on a combo nothing has
    filled gets -1, selects nothing, and then asserts against a figure that was
    never drawn — which reads as the feature being broken.
    """
    rctl.redraw_response(tab.ctx)
    index = tab.series.findData(variable_id)
    assert index >= 0, (
        f"{variable_id!r} was not offered in the Series combo: "
        f"{[tab.series.itemData(i) for i in range(tab.series.count())]}")
    tab.series.setCurrentIndex(index)


def _load_ungrouped(tab, variable, assignments, timepoints=(24.0,)):
    """A plate with the variable filled and wells lit, and **no groups**.

    The state a user is in before they have defined a single Plot Group, and
    the one every other loader in this file skips: ``_load`` builds groups from
    its own argument and leaves ``selected_wells`` empty, so the whole file was
    a test of grouped plates only.
    """
    tab.ctx.state.dataset.all_timepoints_cache = [float(t) for t in timepoints]
    wells = sorted(assignments)
    tab.ctx.state.dataset.well_paths = {w: f"/{w}.csv" for w in wells}
    tab.ctx.state.selection.variables = sv.VariableSet(
        variables=(variable,), assignments={variable.id: assignments})
    tab.ctx.state.selection.selections = []
    tab.ctx.state.selection.selected_wells = set(wells)
    tab.ctx.runtime.barplot.current_keys = lambda: []


# ── the X combo ─────────────────────────────────────────────────────────────

def test_the_x_combo_lists_only_number_variables(tab):
    """A Text variable groups and colours fine; it has nowhere to sit on a
    numeric axis, and offering it would order the x by whatever the user
    happened to type first."""
    text = _variable(kind=sv.TEXT, name="Cell line", unit="",
                     pairs=(("HeLa", None),))
    number = _variable()
    tab.ctx.state.selection.variables = sv.VariableSet(variables=(text, number))
    rctl.refresh_x_combo(tab.ctx)

    assert [tab.combo.itemData(i) for i in range(tab.combo.count())] == ["dose"]


def test_with_no_number_variable_the_combo_says_where_to_make_one(tab):
    """A user here has no way to guess the answer is on a sub-tab of another
    pane, and an empty dropdown reads as the app being broken."""
    rctl.refresh_x_combo(tab.ctx)

    assert tab.combo.isEnabled() is False
    assert "Home → Variables" in tab.combo.itemText(0)


def test_the_users_pick_survives_a_refresh(tab):
    a = _variable(name="Dose")
    b = _variable(name="Density", unit="cells/well")
    tab.ctx.state.selection.variables = sv.VariableSet(variables=(a, b))
    rctl.refresh_x_combo(tab.ctx)
    tab.combo.setCurrentIndex(1)
    rctl.refresh_x_combo(tab.ctx)

    assert tab.combo.currentData() == "density"


def test_a_variable_that_is_deleted_falls_back_to_the_first(tab):
    a = _variable(name="Dose")
    tab.ctx.state.selection.variables = sv.VariableSet(variables=(a,))
    rctl.refresh_x_combo(tab.ctx)
    tab.combo.setCurrentIndex(0)
    tab.ctx.state.selection.variables = sv.VariableSet()
    rctl.refresh_x_combo(tab.ctx)

    assert tab.combo.currentData() == ""


# ── the redraw actually reaches the figure ──────────────────────────────────

def test_redrawing_puts_the_points_on_the_axes(tab):
    variable = _variable(pairs=(("1", 1.0), ("10", 10.0)))
    _load(tab, variable, {"A01": "v0", "A02": "v1"},
          [("low", ["A01"]), ("high", ["A02"])])
    rctl.redraw_response(tab.ctx)

    assert tab.ax.has_data(), (
        "the redraw ran and drew nothing, so the figure is a picture of a plot"
    )
    assert tab.ax.get_xlabel() == "Dose (µM)"


def test_changing_x_redraws(tab):
    variable = _variable(pairs=(("1", 1.0),))
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])])
    rctl.refresh_x_combo(tab.ctx)
    tab.ax.clear()

    rctl.on_x_variable_changed(tab.ctx)
    assert tab.ax.has_data()


def test_the_picker_seeds_from_the_shared_focus_timepoint(tab):
    """The seed follows ``focus_timepoint``, not the earliest timepoint.

    Six is deliberately neither the first nor the last of the four the dataset
    holds, so a picker that fell back to "the first available" — the branch
    taken when the shared focus is missing — reads as a different number here
    rather than as the same one by luck.
    """
    variable = _variable()
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])],
          timepoints=(0.0, 6.0, 24.0, 48.0))
    tab.ctx.state.plot.focus_timepoint = 6.0
    rctl.redraw_response(tab.ctx)

    assert rctl.timepoints_for(tab.ctx) == [6.0]


# ── what it could not draw ──────────────────────────────────────────────────

def test_an_omitted_group_reaches_the_figure_and_not_the_status_line(tab):
    """The status bar is persistent, and a successful redraw has no reason to
    write to it — so a sentence pushed there outlives its condition. Fill in
    the missing dose and the caption clears while the bar goes on reporting it,
    contradicting the plot above with no redraw able to correct it. The caption
    is the place: the redraw that resolves the condition clears it."""
    variable = _variable(pairs=(("1", 1.0),))
    _load(tab, variable, {"A01": "v0"},
          [("ok", ["A01"]), ("short", ["A02"])])
    rctl.redraw_response(tab.ctx)

    captions = " | ".join(t.get_text() for t in tab.ax.texts)
    assert "omitted" in captions and "short" in captions, (
        "a group was dropped from the plot and the figure did not say so"
    )
    assert tab.statuses == [], (
        "the shortfall was pushed to the persistent status bar, where it "
        "outlives the condition"
    )


def test_a_complete_plate_says_nothing(tab):
    variable = _variable(pairs=(("1", 1.0),))
    _load(tab, variable, {"A01": "v0"}, [("ok", ["A01"])])
    rctl.redraw_response(tab.ctx)
    assert tab.statuses == []


def test_the_csv_carries_a_row_for_every_group_that_was_left_out(tab,
                                                                 monkeypatch):
    """A CSV of eight groups that lists five is the same silent substitution as
    a figure of eight that draws five — and a spreadsheet is exactly where
    someone counts rows and believes the total."""
    written = {}

    # Patched on the controller, not on ``core.csv_export``: the controller
    # imports the name at module scope, so rebinding the source module would
    # leave the already-bound reference untouched and this test would measure
    # the real writer's file dialog.
    monkeypatch.setattr(rctl, "write_csv_rows",
                        lambda ctx, stem, header, rows: written.update(
                            stem=stem, header=header, rows=rows) or True)

    variable = _variable(pairs=(("1", 1.0), ("vehicle", None)))
    _load(tab, variable, {"A01": "v0", "A03": "v1"},
          [("ok", ["A01"]), ("short", ["A02"]), ("veh", ["A03"])])
    rctl.refresh_x_combo(tab.ctx)          # as the redraw does, before exporting
    rctl.export_response_csv(tab.ctx)

    members = {r[0]: r[-1] for r in written["rows"]}
    assert members["ok"] == ""
    assert members["short"] == "no value"
    assert members["veh"] == "value is not a number"
    assert "reason" in written["header"]


def test_exporting_with_no_x_chosen_is_refused_rather_than_written_empty(
        tab, monkeypatch):
    """A CSV with a header and no rows is indistinguishable from a plate that
    genuinely had nothing to say."""
    called = []
    monkeypatch.setattr(rctl, "write_csv_rows",
                        lambda *a, **k: called.append(a) or True)

    rctl.export_response_csv(tab.ctx)

    assert called == [], "an empty CSV was written"
    assert any("Pick a Number variable under X" in s for s in tab.statuses)


# ── the timepoint picker ────────────────────────────────────────────────────

def test_it_opens_on_one_timepoint_not_all_of_them(tab):
    """A twelve-timepoint plate opened as twelve overlapping curves per
    condition is not a plot anyone can read."""
    variable = _variable()
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])],
          timepoints=(0.0, 6.0, 24.0, 48.0))
    rctl.redraw_response(tab.ctx)

    assert rctl.timepoints_for(tab.ctx) == [24.0], (
        "the picker seeded with something other than the shared focus timepoint"
    )
    assert tab.w("_response_tp_button").text() == "Timepoints: 1/4 selected"


def test_a_plate_with_no_timepoint_field_still_has_a_timepoint_to_draw(tab):
    """The reported defect: "only shows the number 0", on a plate every other
    tab drew.

    A dataset whose filename schema carries no timepoint slot has
    ``timepoint_hours`` NaN in every row, so ``all_timepoints_cache`` is empty —
    and ``data_loading.resolved_timepoints`` files those rows at **0.0**, which
    is where the aggregator keeps their points. Bar Plots has always added that
    0.0 back; this tab asked ``get_all_timepoints``, which reads the raw column
    only, got nothing, and offered nothing: a picker with no entries, a button
    reading 0, and an empty figure with no explanation on it.

    The wrong implementation this rules out is the one that shipped: an
    ``available_timepoints`` that reports what the numeric column holds rather
    than what the plots can ask for. The fixture is a plate with data and no
    timepoint field, which is the case where the two answers differ.
    """
    variable = _variable()
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])], timepoints=())
    assert tab.ctx.state.dataset.all_timepoints_cache == []

    rctl.redraw_response(tab.ctx)

    assert rctl.available_timepoints(tab.ctx) == [0.0]
    assert rctl.timepoints_for(tab.ctx) == [0.0], (
        "the picker offered no timepoint on a plate whose points are all at 0.0"
    )
    assert tab.w("_response_tp_button").text() == "Timepoints: All 1 selected"
    assert tab.ax.has_data(), "the figure is empty on a plate that has data"


def test_the_button_says_how_many_of_how_many(tab):
    """"0 selected" cannot say whether the rest are unticked or not there.

    Which is the whole of the reported symptom: the button read 0 both when a
    user had unticked everything and when the tab had found nothing to offer,
    and those have different fixes. The shared readout names the total.
    """
    variable = _variable()
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])],
          timepoints=(0.0, 24.0, 48.0))
    rctl.redraw_response(tab.ctx)
    assert tab.w("_response_tp_button").text() == "Timepoints: 1/3 selected"

    tab.ctx.state.feature_slice("response").tp_selections = {
        "0": True, "24": True, "48": True}
    rctl.update_timepoint_button(tab.ctx)
    assert tab.w("_response_tp_button").text() == "Timepoints: All 3 selected"


def test_the_shared_focus_timepoint_is_never_written(tab):
    """Writing it moves Heat Map and Distribution to whatever this tab opened on.

    The shared focus is set to a timepoint this dataset does not hold, so the
    picker must fall back and seed with ``0`` — which means any write of the
    seed, or of the user's later pick, lands a *different* number in the field
    and this test sees it. Asserting the field still reads what it read before
    is worth nothing when the value the tab would write is the same value.
    """
    variable = _variable()
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])],
          timepoints=(0.0, 24.0))
    tab.ctx.state.plot.focus_timepoint = 999.0
    rctl.redraw_response(tab.ctx)

    assert rctl.timepoints_for(tab.ctx) == [0.0]
    assert tab.ctx.state.plot.focus_timepoint == 999.0

    slice_ = tab.ctx.state.feature_slice("response")
    slice_.tp_selections = {"0": False, "24": True}
    rctl.redraw_response(tab.ctx)

    assert rctl.timepoints_for(tab.ctx) == [24.0]
    assert tab.ctx.state.plot.focus_timepoint == 999.0


def test_selecting_two_timepoints_draws_two_curves(tab):
    variable = _variable(pairs=(("1", 1.0), ("10", 10.0)))
    _load(tab, variable, {"A01": "v0", "A02": "v1"},
          [("low", ["A01"]), ("high", ["A02"])], timepoints=(0.0, 24.0))
    rctl.redraw_response(tab.ctx)
    tab.ctx.state.feature_slice("response").tp_selections = {"0": True, "24": True}

    data = rctl.response_data(tab.ctx)
    assert [c.timepoint for c in data.curves] == [0.0, 24.0]
    assert all(len(c.points) == 2 for c in data.curves)


def test_turning_every_timepoint_off_is_a_choice_and_is_left_alone(tab):
    """A populated selection with nothing on means "show nothing" — re-seeding
    it would override the user on every redraw."""
    variable = _variable()
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])], timepoints=(0.0, 24.0))
    rctl.redraw_response(tab.ctx)
    tab.ctx.state.feature_slice("response").tp_selections = {"0": False, "24": False}

    rctl.redraw_response(tab.ctx)
    assert rctl.timepoints_for(tab.ctx) == []
    assert tab.w("_response_tp_button").text() == "Timepoints: 0/2 selected"


def test_a_timepoint_the_new_dataset_lacks_is_dropped_from_the_selection(tab):
    """Otherwise the picker offers a timepoint this plate does not have, and the
    curve for it is silently empty."""
    variable = _variable()
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])], timepoints=(0.0, 24.0))
    rctl.redraw_response(tab.ctx)
    tab.ctx.state.feature_slice("response").tp_selections = {"0": True, "24": True}

    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])], timepoints=(0.0,))
    rctl.redraw_response(tab.ctx)

    assert rctl.timepoints_for(tab.ctx) == [0.0]


# ── the log-x toggle ────────────────────────────────────────────────────────

def _prefs(tab):
    from well_viewer.core.plot_style import figure_prefs

    return figure_prefs(tab.ctx, tab.ax.figure)


def _decade_data():
    from well_viewer.features.response.collect import (
        ResponseCurve, ResponseData, ResponsePoint,
    )

    points = tuple(ResponsePoint(f"m{i}", x, 5.0, 0.1, 3, f"{x:g}")
                   for i, x in enumerate((0.1, 1.0, 10.0)))
    return ResponseData("Dose", "µM", [ResponseCurve(24.0, points)])


def _one_decade_data():
    from well_viewer.features.response.collect import (
        ResponseCurve, ResponseData, ResponsePoint,
    )

    points = tuple(ResponsePoint(f"m{i}", x, 5.0, 0.1, 3, f"{x:g}")
                   for i, x in enumerate((1.0, 5.0)))
    return ResponseData("Temp", "", [ResponseCurve(24.0, points)])


def test_an_untouched_toggle_asks_the_data(tab):
    """Defaulting to linear means every dose series opens unreadable and every
    user fixes it by hand before they can see anything."""
    assert rctl.resolved_log_x(tab.ctx, tab.ax.figure, _decade_data()) is True
    assert rctl.resolved_log_x(tab.ctx, tab.ax.figure, _one_decade_data()) is False


def test_the_users_choice_stands_over_the_data(tab):
    """A tab that re-derived the toggle from the data every redraw would be a
    control that does not stay where it is put."""
    _prefs(tab)["x_log"] = False
    assert rctl.resolved_log_x(tab.ctx, tab.ax.figure, _decade_data()) is False

    _prefs(tab)["x_log"] = True
    assert rctl.resolved_log_x(tab.ctx, tab.ax.figure, _one_decade_data()) is True


def test_off_is_distinguishable_from_never_touched(tab):
    """§7's rule that an undefined value is not a computed one, applied to a
    preference. ``figure_prefs`` reads an unset axis key as its neutral default,
    which is right for drawing and makes the two identical to a caller whose own
    default is not that — so the distinction needs its own accessor."""
    from well_viewer.core.plot_style import figure_pref_is_set

    fig = tab.ax.figure
    assert figure_pref_is_set(tab.ctx, fig, "x_log") is False
    _prefs(tab)["x_log"] = False
    assert figure_pref_is_set(tab.ctx, fig, "x_log") is True
    assert _prefs(tab).get("x_log") is False


def test_the_chosen_scale_survives_the_shared_restyle(tab):
    """``apply_export_style_prefs`` re-reads the shared ``x_log`` on every
    restyle; without ``_aw_force_xlog`` it resets an axis this tab chose from
    the data, taking the off-scale column's ticks with it."""
    from well_viewer.features.response.renderer import draw_response

    draw_response(None, tab.ax, _decade_data(), log_x=True)
    assert getattr(tab.ax, "_aw_force_xlog", None) is True


def test_the_dose_ticks_say_what_the_user_typed(tab):
    """``ResponsePoint.label`` exists for this and nothing read it.

    The fixture's labels are ones the parse does **not** round-trip — ``0.10``
    keeps its trailing zero, ``1e0`` is written as an exponent, ``10 µM``
    carries its unit — because with labels the float formatter reproduces
    (``0.1``/``1``/``10``) the fixed and the broken renderer draw the same
    ticks and no assertion of any strength could tell them apart.
    """
    from well_viewer.features.response.collect import (
        ResponseCurve, ResponseData, ResponsePoint,
    )
    from well_viewer.features.response.renderer import draw_response

    points = tuple(
        ResponsePoint(f"m{i}", x, 5.0, 0.1, 3, text)
        for i, (x, text) in enumerate(
            ((0.1, "0.10"), (1.0, "1e0"), (10.0, "10 µM"))))
    draw_response(None, tab.ax,
                  ResponseData("Dose", "µM", [ResponseCurve(24.0, points)]),
                  log_x=True)

    assert [t.get_text() for t in tab.ax.get_xticklabels()] == [
        "0.10", "1e0", "10 µM"]


def test_a_dose_two_members_typed_differently_falls_back_to_the_number(tab):
    """One tick cannot say both ``0.3`` and ``0.30``, so it says what is true
    of both. Silently picking one would put a label on the axis that half the
    plate's wells were not given."""
    from well_viewer.features.response.collect import (
        ResponseCurve, ResponseData, ResponsePoint,
    )
    from well_viewer.features.response.renderer import draw_response

    # ``0.30`` is *first*, so the fallback and "keep whichever label arrived
    # first" produce different ticks. With ``0.3`` first they agree, and
    # deleting the collision check leaves this green — which is what the first
    # version of this test did.
    points = (
        ResponsePoint("a", 0.3, 5.0, 0.1, 3, "0.30"),
        ResponsePoint("b", 0.3, 6.0, 0.1, 3, "0.3"),
        ResponsePoint("c", 30.0, 7.0, 0.1, 3, "30"),
    )
    draw_response(None, tab.ax,
                  ResponseData("Dose", "", [ResponseCurve(24.0, points)]),
                  log_x=True)

    assert [t.get_text() for t in tab.ax.get_xticklabels()] == ["0.3", "30"]


# ── nothing to draw ─────────────────────────────────────────────────────────

def test_a_plate_with_no_values_draws_the_reason_not_empty_axes(tab):
    """Pressed through the real redraw, not by calling the renderer directly.

    ``draw_response`` taking the flags is one thing; the controller passing the
    *right* flags is another, and only this route proves the second.
    """
    variable = _variable()
    _load(tab, variable, {}, [("a", ["A01"]), ("b", ["A02"])])
    rctl.redraw_response(tab.ctx)

    texts = [t.get_text() for t in tab.ax.texts]
    assert any("No group has a Dose value" in s for s in texts), texts
    assert any("Home → Variables" in s for s in texts), texts
    assert not tab.ax.has_data(), "it drew points as well as the reason"


def test_turning_every_timepoint_off_says_so_rather_than_blaming_the_fill(tab):
    """The user's own choice, reported as their choice.

    The plate here is filled perfectly; a sentence sending them to the
    Variables tab would send them to fix something that is not broken.
    """
    variable = _variable()
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])])
    rctl.redraw_response(tab.ctx)
    slice_ = tab.ctx.state.feature_slice("response")
    slice_.tp_selections = {k: False for k in slice_.tp_selections}
    tab.statuses.clear()

    rctl.redraw_response(tab.ctx)

    texts = " | ".join(t.get_text() for t in tab.ax.texts)
    assert "Timepoints" in texts, texts
    assert "Variables" not in texts, texts


# ── the fit ─────────────────────────────────────────────────────────────────
#
# Built through ``tab_view._build_fit_popover`` and driven by setting the combo
# and the line edits, not by calling the controller. §7.6 is the reason: the
# per-sample control editor shipped unopenable past a lane of ~4,950 tests
# because every test of it entered one step past the closure that was broken.
# Asserting a model is *offered* is not asserting that choosing it fits
# anything.

@pytest.fixture
def fit_ui(tab, qtbot):
    """The real Fit popover, wired to the real controller."""
    from widgets.control_band import ControlBand

    from well_viewer.features.response import tab_view as rtv

    band = ControlBand()
    qtbot.addWidget(band)
    tab.ctx.window = band          # ``_build_fit_popover`` parents into the band
    button = rtv._build_fit_popover(tab.ctx, band.slot("response"))
    return SimpleNamespace(button=button,
                           combo=tab.w("_response_fit_cb"),
                           top=tab.w("_response_fit_top"),
                           bottom=tab.w("_response_fit_bottom"))


def _dose_plate(tab, *, ec50=1.0, bottom=5.0, top=95.0, flat=False):
    """A plate whose eight groups sit on a known 4PL curve."""
    import math

    doses = [0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0]
    variable = _variable(pairs=tuple((f"{d:g}", d) for d in doses))
    wells = [f"A{i + 1:02d}" for i in range(len(doses))]
    values = {}
    for i, (well, dose) in enumerate(zip(wells, doses)):
        values[well] = f"v{i}"
    _load(tab, variable, values,
          [(f"{d:g}", [w]) for d, w in zip(doses, wells)])

    def _y(dose):
        if flat:
            return 50.0
        return bottom + (top - bottom) / (
            1 + 10 ** ((math.log10(ec50) - math.log10(dose)) * 1.0))

    by_name = {f"{d:g}": _y(d) for d in doses}
    import well_viewer.domain.stats_compute as sc
    tab.monkeypatch.setattr(
        sc, "compute_rep_stats",
        lambda ctx, rset, t, thr, sem, **kw: (by_name[rset.name], 1.0, 0.5, 0.1))
    return doses


def test_choosing_a_model_in_the_popover_draws_a_fitted_line(tab, fit_ui):
    """Pressed through the combo, so the signal that shipped unconnected once
    is the thing under test."""
    _dose_plate(tab, ec50=2.5)
    rctl.redraw_response(tab.ctx)
    before = len(tab.ax.lines)

    index = fit_ui.combo.findData("4pl")
    assert index >= 0, "the 4PL model was not offered"
    fit_ui.combo.setCurrentIndex(index)

    assert len(tab.ax.lines) > before, "choosing 4PL drew nothing new"
    assert tab.ctx.state.feature_slice("response").fit_model == "4pl"


def _legend_labels(ax):
    """The legend's entries, or a named failure rather than an AttributeError.

    A missing legend is one of the ways the EC50 goes missing — it is where the
    number is written — so the absence has to read as a failed assertion about
    the figure, not as a crash inside the test's own plumbing (§7.5 #1).
    """
    legend = ax.get_legend()
    assert legend is not None, "the figure drew no legend, so no EC50 with it"
    return [t.get_text() for t in legend.get_texts()]


def test_the_ec50_reaches_the_legend(tab, fit_ui):
    _dose_plate(tab, ec50=2.5)
    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("4pl"))

    labels = _legend_labels(tab.ax)
    assert any("EC50" in s for s in labels), labels
    assert any("2.5" in s for s in labels), labels


def test_a_flat_plate_puts_a_dash_in_the_legend_and_the_reason_underneath(
        tab, fit_ui):
    """Never the optimiser's last iterate. This is the defect that shipped
    once: a flat series printed EC50 0.548 µM from eight measurements that
    showed no response at all."""
    _dose_plate(tab, flat=True)
    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("4pl"))

    labels = _legend_labels(tab.ax)
    assert any("EC50 — (no fit)" in s for s in labels), labels
    captions = [t.get_text() for t in tab.ax.texts]
    assert any("flat" in s for s in captions), captions


def test_a_refused_fit_draws_no_line(tab, fit_ui):
    """A dashed line through the points would assert a shape nothing supports."""
    _dose_plate(tab, flat=True)
    rctl.redraw_response(tab.ctx)
    before = len(tab.ax.lines)

    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("4pl"))

    assert len(tab.ax.lines) == before, "a curve was drawn for a refused fit"


def test_pinning_a_plateau_in_the_popover_changes_the_fit(tab, fit_ui):
    """The line edit, not ``set_fit_bound``: the editingFinished wiring is the
    half that can ship dead."""
    _dose_plate(tab, ec50=1.0, top=95.0)
    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("4pl"))

    fit_ui.top.setText("200")
    fit_ui.top.editingFinished.emit()

    assert tab.ctx.state.feature_slice("response").fit_top == 200.0
    data = rctl.response_data(tab.ctx)
    assert rctl.response_fits(tab.ctx, data)[(24.0, "")].top == 200.0


def test_clearing_a_plateau_frees_it_again(tab, fit_ui):
    _dose_plate(tab)
    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("4pl"))
    fit_ui.top.setText("200")
    fit_ui.top.editingFinished.emit()

    fit_ui.top.setText("")
    fit_ui.top.editingFinished.emit()

    assert tab.ctx.state.feature_slice("response").fit_top is None


def test_an_unreadable_plateau_frees_the_parameter_and_says_so(tab, fit_ui):
    """Keeping the last good value would fit a curve to a number the user did
    not type, with nothing on screen to say the field was ignored."""
    _dose_plate(tab)
    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("4pl"))
    fit_ui.top.setText("100")
    fit_ui.top.editingFinished.emit()
    tab.statuses.clear()

    fit_ui.top.setText("1OO")
    fit_ui.top.editingFinished.emit()

    assert tab.ctx.state.feature_slice("response").fit_top is None
    assert any("not a number" in s for s in tab.statuses), tab.statuses


def test_turning_the_fit_back_off_removes_the_line_and_the_ec50(tab, fit_ui):
    _dose_plate(tab, ec50=2.5)
    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("4pl"))
    with_fit = len(tab.ax.lines)

    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("none"))

    assert len(tab.ax.lines) < with_fit
    legend = tab.ax.get_legend()
    labels = [] if legend is None else [t.get_text() for t in legend.get_texts()]
    assert not any("EC50" in s for s in labels), labels


def test_the_csv_carries_the_ec50_and_the_reason_it_has_none(tab, fit_ui,
                                                             monkeypatch):
    """A spreadsheet of fits that are all successful is the same silent
    substitution as a figure that draws five of eight groups."""
    written = {}
    monkeypatch.setattr(rctl, "write_csv_rows",
                        lambda ctx, name, header, rows: written.update(
                            header=header, rows=rows))
    _dose_plate(tab, ec50=2.5)
    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("4pl"))

    rctl.export_response_csv(tab.ctx)

    fit_rows = [r for r in written["rows"] if r[0].startswith("fit")]
    assert len(fit_rows) == 1, written["rows"]
    # Columns shifted one right by the new `series` column, which is blank
    # here because no series variable is chosen.
    assert written["header"][1] == "series"
    assert fit_rows[0][1] == ""
    assert float(fit_rows[0][3]) == pytest.approx(2.5, rel=1e-2)
    assert "EC50" in fit_rows[0][4]


def test_the_csv_row_for_a_refused_fit_carries_the_reason_and_no_number(
        tab, fit_ui, monkeypatch):
    written = {}
    monkeypatch.setattr(rctl, "write_csv_rows",
                        lambda ctx, name, header, rows: written.update(rows=rows))
    _dose_plate(tab, flat=True)
    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("4pl"))

    rctl.export_response_csv(tab.ctx)

    row = next(r for r in written["rows"] if r[0].startswith("fit"))
    assert row[1] == "", f"a fit row carried a series with none chosen: {row}"
    assert row[3] == "", f"a refused fit wrote an EC50: {row}"
    assert "flat" in row[-1], row


def test_the_fit_settings_are_one_set_for_the_whole_figure(tab, fit_ui):
    """Two timepoints fitted with independently floating tops give two EC50s
    that cannot be compared, and comparing them is the only reason to draw
    both."""
    _dose_plate(tab, ec50=2.5)
    tab.ctx.state.dataset.all_timepoints_cache = [24.0, 48.0]
    slice_ = tab.ctx.state.feature_slice("response")
    slice_.tp_selections = {"24": True, "48": True}
    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("4pl"))
    fit_ui.bottom.setText("0")
    fit_ui.bottom.editingFinished.emit()

    fits = rctl.response_fits(tab.ctx, rctl.response_data(tab.ctx))

    assert set(fits) == {(24.0, ""), (48.0, "")}
    assert {f.bottom for f in fits.values()} == {0.0}
    assert {f.model for f in fits.values()} == {"4pl"}


# ── fold change ─────────────────────────────────────────────────────────────

def test_the_fold_change_popover_is_built_and_registers_this_tabs_combos(
        tab, qtbot):
    """Built through the real installer, so the scope's widget names are the
    ones the cross-tab sync actually looks for.

    ``_sync_widgets_to_state`` finds each scope's combos by
    ``_fc_ctrl_combo_<scope>`` / ``_fc_baseline_combo_<scope>``. A scope
    registered under one name whose installer registers another is a control
    that changes nothing on the other tabs — offered, connected, and inert.
    """
    from widgets.control_band import ControlBand

    from well_viewer.features.response import tab_view as rtv

    band = ControlBand()
    qtbot.addWidget(band)
    tab.ctx.window = band
    layout = band.layout()
    rtv.build_response_tab  # the popover is built inside it; exercise directly
    from well_viewer.core.fold_change_controls_view import (
        install_fold_change_controls,
    )
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    panel = QWidget(band)
    qtbot.addWidget(panel)
    install_fold_change_controls(tab.ctx, panel, QVBoxLayout(panel),
                                 scope="response")

    assert tab.w("_fc_ctrl_combo_response") is not None
    assert tab.w("_fc_baseline_combo_response") is not None
    assert layout is not None


def test_the_response_scope_is_the_features_own(tab):
    """Registered through the feature descriptor, so removing the feature drops
    the scope with it — the one-line-removable property the repo enforces."""
    from well_viewer.features.registry import build_feature_registry

    descriptor = build_feature_registry().get("Response Curves")
    assert descriptor is not None
    assert descriptor.fold_change_scope_factory is not None
    scope = descriptor.fold_change_scope_factory()
    assert scope.name == "response"
    assert scope.tab_names == ("Response Curves",)
    assert scope.ctrl_combo_attr == "_fc_ctrl_combo_response"


def test_a_normalised_axis_says_so_in_its_label(tab):
    """A normalised value under a raw axis label is a number that does not mean
    what the axis says it means — and the state is shared, so this axis can
    change without anything on this page being touched."""
    variable = _variable()
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])],
          timepoints=(0.0, 24.0))
    tab.ctx.state.plot.fc_vs_t0_on = True
    slice_ = tab.ctx.state.feature_slice("response")
    slice_.tp_selections = {"0": True, "24": True}

    rctl.redraw_response(tab.ctx)

    label = rctl._y_label(tab.ctx, rctl.response_data(tab.ctx))
    assert label != rctl._y_label(tab.ctx)
    assert "GFP" in label


def test_declining_vs_t0_reaches_the_figure_not_only_the_data(tab):
    variable = _variable()
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])])
    tab.ctx.state.plot.fc_vs_t0_on = True

    rctl.redraw_response(tab.ctx)

    captions = " | ".join(t.get_text() for t in tab.ax.texts)
    assert "vs t0" in captions, captions


def test_the_pooling_notice_reaches_the_figure(tab):
    variable = _variable()
    _load(tab, variable, {"A01": "v0", "A02": "v0"}, [("both", ["A01", "A02"])])
    tab.ctx.state.plot.bar_pool_replicates = False

    rctl.redraw_response(tab.ctx)

    captions = " | ".join(t.get_text() for t in tab.ax.texts)
    assert "pooled" in captions, captions


# ── saving and restoring the figure ─────────────────────────────────────────
#
# A saved plot that comes back different from the one that was saved is the
# silent substitution §7 forbids, and this tab is the most exposed to it: the
# fit is the point of the page and nothing about it is recoverable from the
# plate. Restoring the *slice* is only half — the popover's widgets are seeded
# when it is built and never read the slice again, so a restore that stopped
# there drew a 4PL above a control reading None.

def test_the_snapshot_and_the_restore_are_the_features_own(tab):
    """Reached through the registry hook, so removing the feature drops the
    serialization with it and ``persistence`` never names this tab."""
    from well_viewer.features.registry import build_feature_registry

    descriptor = build_feature_registry().get("Response Curves")
    assert descriptor.view_state_io is not None
    snapshot, apply = descriptor.view_state_io()
    assert callable(snapshot) and callable(apply)


def test_the_fit_survives_a_save_and_a_restore(tab, fit_ui):
    from well_viewer.features.response import view_state_io as vsio

    _dose_plate(tab, ec50=2.5)
    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("4pl"))
    fit_ui.top.setText("200")
    fit_ui.top.editingFinished.emit()

    state = vsio.snapshot_response(tab.ctx)

    slice_ = tab.ctx.state.feature_slice("response")
    slice_.fit_model = "none"
    slice_.fit_top = None
    vsio.apply_response(tab.ctx, state)

    assert slice_.fit_model == "4pl"
    assert slice_.fit_top == 200.0


def test_the_restore_moves_the_controls_not_only_the_state(tab, fit_ui):
    """The half that shipped broken on the bar tab: the figure drew one thing
    and the control the user would reach for showed another."""
    from well_viewer.features.response import view_state_io as vsio

    _dose_plate(tab)
    state = {"fit_model": "hill", "fit_bottom": 0.0, "tp_selections": {"24": True}}

    vsio.apply_response(tab.ctx, state)

    assert fit_ui.combo.currentData() == "hill"
    assert fit_ui.bottom.text() == "0"


def test_an_unpinned_plateau_is_not_stored_as_zero(tab, fit_ui):
    """``None`` and ``0.0`` are different requests — "fit this plateau" and
    "this plateau is zero" — and writing 0.0 for an unset bottom would turn
    every restored 4PL into a Hill."""
    from well_viewer.features.response import view_state_io as vsio

    _dose_plate(tab)
    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("4pl"))

    state = vsio.snapshot_response(tab.ctx)

    assert "fit_bottom" not in state and "fit_top" not in state


def test_the_x_variable_is_stored_by_id_not_by_name(tab, fit_ui):
    """A name is renamed and an index moves when another variable is added;
    either would restore the figure against a different variable while looking
    as though nothing had gone wrong."""
    from well_viewer.features.response import view_state_io as vsio

    _dose_plate(tab)
    rctl.redraw_response(tab.ctx)
    state = vsio.snapshot_response(tab.ctx)

    assert state["x_variable_id"] == "dose"
    assert "Dose" not in str(state.get("x_variable_id"))


def test_a_hand_edited_block_frees_the_plateau_rather_than_refusing(tab, fit_ui):
    from well_viewer.features.response import view_state_io as vsio

    _dose_plate(tab)
    vsio.apply_response(tab.ctx, {"fit_model": "4pl", "fit_top": "not a number"})

    slice_ = tab.ctx.state.feature_slice("response")
    assert slice_.fit_model == "4pl"
    assert slice_.fit_top is None


def test_the_restore_does_not_redraw_from_half_the_state(tab, fit_ui):
    """Each control emits a redraw, and a figure drawn part-way through a
    restore looks like the restore itself went wrong."""
    from well_viewer.features.response import view_state_io as vsio

    _dose_plate(tab)
    # The combos have to be *populated and on a different value*, or the
    # restore's ``setCurrentIndex`` is never reached and the test passes on a
    # branch it did not run — which is how the first version of it passed
    # against a restore with every ``blockSignals`` removed.
    rctl.redraw_response(tab.ctx)
    fit_ui.combo.setCurrentIndex(fit_ui.combo.findData("4pl"))
    tab.ctx.state.selection.variables = sv.VariableSet(
        variables=(_variable(name="Other", pairs=(("1", 1.0),)),
                   tab.ctx.state.selection.variables.variables[0]),
        assignments=dict(tab.ctx.state.selection.variables.assignments))
    rctl.refresh_x_combo(tab.ctx)
    tab.combo.setCurrentIndex(tab.combo.findData("other"))

    draws = []
    tab.monkeypatch.setattr(rctl, "redraw_response",
                            lambda ctx: draws.append(1))

    vsio.apply_response(tab.ctx, {"fit_model": "hill", "tp_selections": {"24": True},
                                  "x_variable_id": "dose"})

    assert tab.combo.currentData() == "dose", "the restore did not move the combo"
    assert fit_ui.combo.currentData() == "hill"
    assert draws == [], f"the restore redrew {len(draws)} time(s) mid-flight"


# ── the tab is repainted by the events every other plot tab is ──────────────

def test_the_tab_redraws_on_activation_and_on_a_selection_change():
    """Without these the figure was whatever it was when the body was built.

    The generic ``redraw_app`` fan-out reaches only Distribution and Heat Map,
    and a tab with no ``on_selection_changed`` hook falls through to exactly
    that — so selecting different wells left the curves drawn from the previous
    selection, which is a plot of the wrong data that looks like a plot of the
    right data.
    """
    from well_viewer.features.registry import build_feature_registry

    descriptor = build_feature_registry().get("Response Curves")
    assert descriptor.on_activate is not None, "the tab is not redrawn when shown"
    assert descriptor.on_selection_changed is not None, (
        "a selection change leaves the curves drawn from the old selection")


def test_the_selection_hook_actually_repaints(tab, monkeypatch):
    """The hook existing is not the hook working — it is a lazy import by name,
    so a rename would leave it importing nothing and failing silently."""
    from well_viewer.features.registry import build_feature_registry

    drawn = []
    monkeypatch.setattr(rctl, "redraw_response", lambda ctx: drawn.append(ctx))

    build_feature_registry().get("Response Curves").on_selection_changed(tab.ctx)

    assert drawn == [tab.ctx]


def test_a_log_axis_the_tab_chose_says_it_chose_it(tab):
    """The Properties ``x_log`` checkbox is bound to the stored preference, and
    an unset preference reads as *off* — so on every dose series spanning two
    decades the axis is logarithmic while the control says it is not.

    Seeding the preference to make them agree would spend the distinction
    ``figure_pref_is_set`` exists for: once set, the axis can never re-derive
    from a plate with different doses. Saying which it is, and where to
    override it, keeps both.
    """
    _dose_plate(tab, ec50=1.0)
    rctl.redraw_response(tab.ctx)

    captions = " | ".join(t.get_text() for t in tab.ax.texts)
    assert "Dose drawn logarithmic" in captions, captions
    assert "Properties" in captions
    # Named for the user's own variable, not for a dose: this tab plots a
    # measurement against any number someone varied.
    assert "doses" not in captions, captions


def test_a_log_axis_the_user_asked_for_says_nothing(tab):
    """Their own answer needs no explanation, and the checkbox already agrees."""
    from well_viewer.core.plot_style import figure_prefs

    _dose_plate(tab, ec50=1.0)
    figure_prefs(tab.ctx, tab.ax.figure)["x_log"] = True
    rctl.redraw_response(tab.ctx)

    captions = " | ".join(t.get_text() for t in tab.ax.texts)
    assert "drawn logarithmic" not in captions, captions


def test_a_linear_axis_says_nothing(tab):
    variable = _variable(pairs=(("1", 1.0), ("2", 2.0)))
    _load(tab, variable, {"A01": "v0", "A02": "v1"},
          [("a", ["A01"]), ("b", ["A02"])])
    rctl.redraw_response(tab.ctx)

    captions = " | ".join(t.get_text() for t in tab.ax.texts)
    assert "logarithmic" not in captions, captions


def test_the_activation_hook_actually_repaints(tab, monkeypatch):
    """Its sibling above drives ``on_selection_changed``; nothing drove this one.

    The test that covers it asserts ``descriptor.on_activate is not None`` — a
    property, satisfied by an ``on_activate`` that shows the sidebar and never
    repaints, which is precisely the pre-fix state the hook was added for.
    Confirmed by mutation: removing ``_redraw_response(ctx)`` from
    ``_activate_response`` left all 50 tests in this file green.

    The hook exists because a leaf built while the plate was empty and shown
    again after a dataset loaded drew nothing, with no control on the page out
    of place to suggest why.
    """
    from PySide6.QtWidgets import QWidget

    from well_viewer.features.registry import build_feature_registry

    # The two pieces of chrome the hook touches on its way to the redraw. Given
    # rather than patched over, so the hook runs as written — patching them out
    # would step past the very sequence under test.
    _frame = QWidget()
    tab.ctx.widgets.get = lambda name, _f=_frame: _f
    from types import SimpleNamespace as _NS
    tab.ctx.coordinator = _NS(refresh_sidebar_map=lambda *a, **k: None)

    drawn = []
    monkeypatch.setattr(rctl, "redraw_response", lambda ctx: drawn.append(ctx))

    build_feature_registry().get("Response Curves").on_activate(tab.ctx)

    assert drawn == [tab.ctx], "activating the tab repainted nothing"


# ── a plate that is filled but not grouped ──────────────────────────────────

def test_lit_wells_with_no_groups_draw_a_curve_rather_than_the_empty_state(tab):
    """The reported defect, pressed through the real redraw.

    A Number variable defined and filled, wells clicked on the plate, no Plot
    Groups. The tab drew nothing and captioned it "No groups to plot — select
    wells on the plate", which names as the fix the thing the user had already
    done. Asserted from the axes rather than from the collector, because the
    controller passing the right flags is the half a collector test cannot see.
    """
    variable = _variable(pairs=(("1", 1.0), ("10", 10.0)))
    _load_ungrouped(tab, variable, {"A01": "v0", "A02": "v1"})
    rctl.redraw_response(tab.ctx)

    assert tab.ax.has_data(), (
        "an annotated plate with wells lit drew nothing"
    )
    texts = [t.get_text() for t in tab.ax.texts]
    assert not any("No groups to plot" in s for s in texts), texts
    # The values, not merely "something was drawn": a figure with the points at
    # the wrong doses passes ``has_data`` just as happily.
    # ``get_lines()[0]`` is the errorbar's own data line; the rest are its
    # caps, which repeat the same x and would let a wrong-dose figure pass a
    # sorted-set assertion.
    assert [float(x) for x in tab.ax.get_lines()[0].get_xdata()] == [1.0, 10.0]


def test_the_ungrouped_csv_names_the_wells_it_drew(tab, tmp_path, monkeypatch):
    """The export travels through the same members, keyed by well token.

    A file whose ``member`` column were blank — or which listed groups that do
    not exist — would be the figure and the spreadsheet disagreeing about what
    was plotted.
    """
    written = {}
    import well_viewer.core.csv_export as ce
    monkeypatch.setattr(
        ce, "write_csv_rows",
        lambda ctx, name, header, rows: written.update(rows=rows))
    import well_viewer.features.response.controller as mod
    monkeypatch.setattr(mod, "write_csv_rows", ce.write_csv_rows)

    variable = _variable(pairs=(("1", 1.0), ("10", 10.0)))
    _load_ungrouped(tab, variable, {"A01": "v0", "A02": "v1"})
    rctl.refresh_x_combo(tab.ctx)
    rctl.export_response_csv(tab.ctx)

    assert [r[0] for r in written["rows"]] == ["A01", "A02"]


def test_an_ungrouped_figure_is_not_captioned_replicates_pooled(tab):
    """The notice describes something that did not happen on this plate.

    It exists because the Bar tab a click away *is* drawing one bar per well
    with this switch off, and the two would disagree with nothing on either to
    explain it. With no group defined both tabs draw one member per lit well,
    so they agree — and a figure whose points are single wells captioned
    "replicates pooled" asserts a pooling nothing performed.
    """
    variable = _variable(pairs=(("1", 1.0), ("10", 10.0)))
    _load_ungrouped(tab, variable, {"A01": "v0", "A02": "v1"})
    tab.ctx.state.plot.bar_pool_replicates = False

    rctl.redraw_response(tab.ctx)

    captions = " | ".join(t.get_text() for t in tab.ax.texts)
    assert "pooled" not in captions, captions
    assert tab.ax.has_data(), "the figure lost its points"


# ── several curves on one figure ────────────────────────────────────────────

def test_choosing_a_series_in_the_combo_splits_the_figure_into_two_curves(tab):
    """Pressed through the combo, not by calling the collector.

    The combo is what a user touches, and an unconnected combo is the exact
    failure this tab has shipped before — the control is offered, the figure
    never changes, and every test that called the collector directly stays
    green.
    """
    _load_two_series(tab)
    rctl.redraw_response(tab.ctx)
    before = len(tab.ax.containers)

    tab.series.setCurrentIndex(tab.series.findData("cl"))

    # ``ax.containers`` is one ErrorbarContainer per curve. Counting
    # ``get_lines()`` instead counts each errorbar's caplines too, which made
    # the unsplit figure look like three curves.
    assert before == 1, "the unsplit figure was not one curve to begin with"
    assert len(tab.ax.containers) == 2, (
        f"choosing a series drew {len(tab.ax.containers)} curves, not two")
    # Each curve spans both doses rather than one holding all four points.
    for container in tab.ax.containers:
        xs = [float(x) for x in container.lines[0].get_xdata()]
        assert xs == [1.0, 10.0], xs


def test_the_series_combo_never_offers_the_variable_on_x(tab):
    """A series on the axis variable is one curve per dose, one point each."""
    _load_two_series(tab)
    rctl.redraw_response(tab.ctx)
    offered = [tab.series.itemData(i) for i in range(tab.series.count())]
    assert "cl" in offered
    assert "dose" not in offered, offered


def test_moving_x_onto_the_series_variable_drops_the_series(tab):
    """The two combos cannot both hold one variable, and X wins.

    Reached by changing X *after* a series is chosen, which is the order a user
    hits it in. Leaving the stale pick would draw one curve per dose.
    """
    _load_two_series(tab)
    _pick_series(tab, "cl")
    assert rctl.selected_series_id(tab.ctx) == "cl"

    tab.combo.blockSignals(True)
    tab.combo.clear()
    tab.combo.addItem("Cell line", "cl")
    tab.combo.blockSignals(False)
    rctl.refresh_series_combo(tab.ctx)

    assert rctl.selected_series_id(tab.ctx) == ""


def test_the_legend_names_the_series_rather_than_only_the_timepoint(tab):
    """With one timepoint drawn, `t=24` on every entry is pure noise."""
    _load_two_series(tab)
    _pick_series(tab, "cl")

    labels = [t.get_text() for t in tab.ax.get_legend().get_texts()]
    assert labels == ["HeLa", "U2OS"], labels


def test_two_series_over_two_timepoints_colour_by_series_and_dash_by_time(tab):
    """Four curves, two colours, two dash patterns — not four unrelated colours.

    The encoding is the feature: a reader has to be able to see which two
    curves are the same cell line and which two are the same moment.
    """
    _load_two_series(tab, timepoints=(24.0, 48.0))
    slice_ = tab.ctx.state.feature_slice("response")
    slice_.tp_selections = {"24": True, "48": True}
    _pick_series(tab, "cl")

    marks = [c.lines[0] for c in tab.ax.containers]
    assert len(marks) == 4, f"expected four curves, got {len(marks)}"
    # Paired with their legend labels, because counting two colours and two
    # dashes is satisfied by **swapping** the encodings — colour on timepoint,
    # dash on series — which is a different figure with the same tallies. A
    # reviewer named that wrong implementation against the previous version of
    # this test, and it passed.
    by_label = {t.get_text(): m
                for t, m in zip(tab.ax.get_legend().get_texts(), marks)}
    assert set(by_label) == {"HeLa · t=24", "HeLa · t=48",
                             "U2OS · t=24", "U2OS · t=48"}, sorted(by_label)
    hela24, hela48 = by_label["HeLa · t=24"], by_label["HeLa · t=48"]
    u2os24 = by_label["U2OS · t=24"]
    assert hela24.get_color() == hela48.get_color(), (
        "one cell line drawn in two colours — colour is not the series")
    assert hela24.get_color() != u2os24.get_color(), (
        "two cell lines share a colour — colour does not separate the series")
    assert hela24.get_linestyle() == u2os24.get_linestyle(), (
        "one timepoint drawn with two dash patterns — dash is not the time")
    assert hela24.get_linestyle() != hela48.get_linestyle(), (
        "two timepoints share a dash pattern — dash does not separate them")
    labels = sorted(t.get_text() for t in tab.ax.get_legend().get_texts())
    assert labels == ["HeLa · t=24", "HeLa · t=48",
                      "U2OS · t=24", "U2OS · t=48"], labels


def test_the_series_reaches_the_exported_csv(tab, monkeypatch):
    """A file whose rows cannot say which curve they came from is unusable."""
    written = {}
    monkeypatch.setattr(rctl, "write_csv_rows",
                        lambda ctx, name, header, rows: written.update(
                            header=header, rows=rows))
    _load_two_series(tab)
    _pick_series(tab, "cl")
    rctl.export_response_csv(tab.ctx)

    assert written["header"][1] == "series"
    point_rows = [r for r in written["rows"] if r[3]]  # rows with an x
    assert {r[1] for r in point_rows} == {"HeLa", "U2OS"}


def test_the_series_choice_survives_a_save_and_a_restore(tab):
    """Stored by id, so a rename or a new variable cannot move it."""
    from well_viewer.features.response import view_state_io as io

    _load_two_series(tab)
    _pick_series(tab, "cl")
    saved = io.snapshot_response(tab.ctx)
    assert saved["series_variable_id"] == "cl"

    tab.series.setCurrentIndex(0)
    assert rctl.selected_series_id(tab.ctx) == ""

    io.apply_response(tab.ctx, saved)
    assert rctl.selected_series_id(tab.ctx) == "cl"


# ── the Y metric, and the spread the toggle asks for ────────────────────────

def test_choosing_the_fraction_in_the_combo_redraws_with_it(tab):
    """Pressed through the combo, and the drawn values checked.

    The mean and the fraction are far apart in the fixture, so a combo wired to
    nothing — this tab's recurring failure — cannot pass by coincidence.
    """
    tab.monkeypatch.setattr(
        __import__("well_viewer.domain.stats_compute", fromlist=["x"]),
        "compute_rep_stats",
        lambda ctx, rset, t, thr, sem, **kw: (10.0, 1.0, 0.42, 0.03))
    variable = _variable(pairs=(("1", 1.0), ("10", 10.0)))
    _load(tab, variable, {"A01": "v0", "A02": "v1"},
          [("lo", ["A01"]), ("hi", ["A02"])])
    rctl.redraw_response(tab.ctx)
    assert [float(y) for y in tab.ax.get_lines()[0].get_ydata()] == [10.0, 10.0]

    tab.y.setCurrentIndex(tab.y.findText(rctl.FRACTION_LABEL))

    assert [float(y) for y in tab.ax.get_lines()[0].get_ydata()] == [0.42, 0.42]


def test_the_axis_label_follows_the_y_metric(tab):
    """An axis naming the Property over a curve of fractions is a lie.

    Asserted as the label text rather than "the label changed", because the
    broken version also changes it — the channel is in both.
    """
    tab.monkeypatch.setattr(
        __import__("well_viewer.domain.stats_compute", fromlist=["x"]),
        "compute_rep_stats",
        lambda ctx, rset, t, thr, sem, **kw: (10.0, 1.0, 0.42, 0.03))
    variable = _variable(pairs=(("1", 1.0),))
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])])

    tab.y.setCurrentIndex(tab.y.findText(rctl.FRACTION_LABEL))
    data = rctl.response_data(tab.ctx)
    assert rctl._y_label(tab.ctx, data) == "GFP fraction above threshold"

    tab.y.setCurrentIndex(tab.y.findText("Mean Intensity"))
    data = rctl.response_data(tab.ctx)
    assert rctl._y_label(tab.ctx, data) == "GFP mean intensity"


def test_the_y_metric_survives_a_save_and_a_restore(tab):
    from well_viewer.features.response import view_state_io as io

    variable = _variable(pairs=(("1", 1.0),))
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])])
    tab.y.setCurrentIndex(tab.y.findText(rctl.FRACTION_LABEL))

    saved = io.snapshot_response(tab.ctx)
    assert saved["metric_label"] == rctl.FRACTION_LABEL

    tab.y.setCurrentIndex(tab.y.findText("Mean Intensity"))
    io.apply_response(tab.ctx, saved)
    assert rctl.response_metric_label(tab.ctx) == rctl.FRACTION_LABEL
    assert tab.y.currentText() == rctl.FRACTION_LABEL


def test_the_fov_toggle_reaches_the_figure(tab):
    """A single well with Spread: FOV on draws a whisker, not a bare point.

    The two producers return different spreads, so a redraw that called the
    well one regardless is distinguishable from one that honoured the toggle.
    """
    import well_viewer.domain.stats_compute as sc
    tab.monkeypatch.setattr(
        sc, "compute_rep_stats",
        lambda ctx, rset, t, thr, sem, **kw: (10.0, float("nan"), 0.5, float("nan")))
    tab.monkeypatch.setattr(
        sc, "compute_rep_per_fov_stats",
        lambda ctx, rset, t, thr, sem, **kw: (10.0, 2.5, 0.5, 0.05))

    variable = _variable(pairs=(("1", 1.0), ("10", 10.0)))
    _load(tab, variable, {"A01": "v0", "A02": "v1"},
          [("lo", ["A01"]), ("hi", ["A02"])])
    tab.ctx.state.plot.use_fov_replicates = True
    rctl.redraw_response(tab.ctx)

    # NOT ``container.has_yerr``: matplotlib sets that True for a yerr list of
    # all-NaN, so it is True on the broken path too and measures nothing. The
    # cap line's actual y positions are the drawn whisker.
    container = tab.ax.containers[0]
    caps = [c.get_ydata() for c in container[1]]
    lows_highs = sorted(float(v) for arr in caps for v in arr)
    assert lows_highs == [7.5, 7.5, 12.5, 12.5], (
        f"the whiskers are not the per-FOV spread of 2.5 about 10.0: {lows_highs}")
    captions = " | ".join(t.get_text() for t in tab.ax.texts)
    assert "no error bar" not in captions, captions


# ── this tab's own Channel and Property ─────────────────────────────────────

def test_the_property_list_offers_the_fraction(tab):
    """The whole reason this pair is tab-local rather than the shared band's.

    The shared combo cannot carry it: its consumers resolve the column as
    ``f"{channel}_{metric}"``, and there is no `gfp_fraction_above_threshold`
    to read.
    """
    rctl.refresh_response_metric_combo(tab.ctx)
    offered = [tab.y.itemText(i) for i in range(tab.y.count())]
    assert rctl.FRACTION_LABEL in offered, offered


def test_the_fraction_reads_the_channels_mean_intensity_column(tab):
    """It is a share of cells clearing the ON cutoff *on that column*.

    Asserting the column, not "a column was chosen": resolving it the obvious
    way — the metric key pasted onto the channel — yields
    ``gfp_fraction_above_threshold``, which no frame has, and every point would
    come back uncomputed with nothing on the figure to say why.
    """
    from well_viewer.data.metric_labels import METRIC_LABEL_TO_KEY

    # What makes the mean column the right answer is that this label is not a
    # metric key at all. Pinned, because without it the explicit branch in
    # ``response_val_col`` is redundant — ``.get(label, "mean_intensity")``
    # already returns the mean — and a test of that branch passes against an
    # implementation that does not have it. The day someone adds a fraction key
    # to the roster, this fires and the branch becomes load-bearing.
    assert rctl.FRACTION_LABEL not in METRIC_LABEL_TO_KEY

    slice_ = tab.ctx.state.feature_slice("response")
    slice_.channel = "GFP"
    slice_.metric_label = rctl.FRACTION_LABEL
    # The **key**, lowercased — not the display label. Composing the column
    # from the label produced "GFP_mean_intensity", which survived only on
    # ``resolve_value_series``'s legacy case-fold fallback and not at all for a
    # ratio, whose label and key are entirely different strings.
    assert rctl.response_val_col(tab.ctx) == "gfp_mean_intensity"

    slice_.metric_label = "Mean Intensity"
    assert rctl.response_val_col(tab.ctx) == "gfp_mean_intensity"
    # A real label from ``METRIC_LABEL_TO_KEY``, not an invented one: the
    # first version of this line said "Integrated Intensity", which is not in
    # the roster, so the fallback correctly returned the mean column and the
    # test was asserting against a vocabulary the app does not have.
    slice_.metric_label = "Total Intensity"
    assert rctl.response_val_col(tab.ctx) == "gfp_total_intensity"


def test_an_untouched_tab_follows_the_shared_channel_and_property(tab):
    """Falls back rather than seeding, so a tab nobody configured is not stale."""
    slice_ = tab.ctx.state.feature_slice("response")
    slice_.channel = ""
    slice_.metric_label = ""
    tab.ctx.state.channel.active_channel = "rfp"
    tab.ctx.state.channel.active_val_col = "rfp_mean_intensity"
    tab.ctx.state.channel.active_metric = "mean_intensity"

    # The **label**, which is what the combo holds. Falling back to the raw
    # token made a case-sensitive ``findText`` miss, so the box read GFP while
    # the tab drew the other channel.
    assert rctl.response_channel(tab.ctx) == "RFP"
    assert rctl.response_metric_label(tab.ctx) == "Mean Intensity"


def test_choosing_a_property_here_does_not_move_the_other_tabs(tab):
    """Tab-local, as the aggregate Scatter tab's pair is.

    Writing the shared ``active_metric`` would repaint Bar and Line to a
    property the user chose on a different page — the surprise this control
    was made tab-local to avoid.
    """
    before_metric = tab.ctx.state.channel.active_metric
    before_channel = tab.ctx.state.channel.active_channel

    tab.y.setCurrentIndex(tab.y.findText(rctl.FRACTION_LABEL))
    tab.channel.setCurrentIndex(tab.channel.findText("RFP"))

    assert tab.ctx.state.channel.active_metric == before_metric
    assert tab.ctx.state.channel.active_channel == before_channel
    assert rctl.response_metric_label(tab.ctx) == rctl.FRACTION_LABEL
    assert rctl.response_channel(tab.ctx) == "RFP"


def test_the_series_default_is_short_enough_not_to_stretch_the_band(tab):
    """``LabeledComboBox`` sizes to the painted text, so a long default is width.

    Pinned as a length rather than left to judgement: the band holds four
    combos and the previous default — "None — one curve per timepoint" — made
    this one several times the width of its neighbours.
    """
    assert len(rctl.NO_SERIES_LABEL) <= 12, rctl.NO_SERIES_LABEL


# ── Group by, driven through the plate-wide control ─────────────────────────

def test_group_by_a_variable_splits_the_curves_rather_than_emptying_the_plot(tab):
    """The reported defect, driven the way the user drives it.

    *Group by* pools every well holding a value, so grouping by `Cell line`
    handed this tab one member per cell line holding both doses — no position
    on a dose axis, refused as "wells disagree", figure blank. Now the members
    are the cross of the grouping with the axis, and the grouping is what tells
    the curves apart.

    Written against the state the band's control writes, not the collector's
    argument, because the defect was that the plate-wide setting never reached
    this tab's collector at all.
    """
    _load_two_series(tab)
    # The redraw is what fills the X combo and mirrors its pick onto the slice,
    # which is where the member seam reads the axis from. Dose is the plate's
    # only Number variable, so it selects itself, exactly as it does on screen.
    rctl.redraw_response(tab.ctx)
    tab.ctx.state.plot.group_by_variable = "cl"

    rctl.redraw_response(tab.ctx)
    data = rctl.response_data(tab.ctx)

    assert [c.series_label for c in data.curves] == ["HeLa", "U2OS"]
    assert all(len(c.points) == 2 for c in data.curves), (
        "a grouped curve does not span the dose series"
    )
    assert data.omitted == []
    assert tab.ax.has_data()


def test_group_by_and_series_are_two_layers_on_this_tab(tab):
    """Group by outermost, the tab's own Series inside it.

    Both name the same variable here, which is the case where a naive "use
    them both" produces `HeLa · HeLa`: the seam drops the duplicate, so the
    figure is two curves rather than two curves with doubled labels.
    """
    _load_two_series(tab)
    rctl.redraw_response(tab.ctx)
    tab.ctx.state.plot.group_by_variable = "cl"
    _pick_series(tab, "cl")

    data = rctl.response_data(tab.ctx)

    assert [c.series_label for c in data.curves] == ["HeLa", "U2OS"]
    assert data.series_name == "Cell line"


def test_the_figure_says_when_a_grouping_could_not_be_crossed(tab):
    """A grouping that matches wells but shares none with the axis.

    The members fall back to the user's own groups, which is the right answer
    and a substitution — the band still reads "Group by: Cell line" over curves
    that are not grouped by it. ``group_by_shortfall`` cannot say this: it asks
    whether the grouping matched any wells, and here it did. It is the
    *crossing* that found none, so the sentence has to be its own.

    The fixture is the case where the two sentences differ. An earlier version
    dropped the cell-line assignments entirely, which makes the grouping match
    nothing — and ``group_by_shortfall``'s existing sentence for that case
    already contains both "Cell line" and "your own groups", so the test passed
    against origin/main with the whole feature absent.
    """
    _load_two_series(tab)
    # Dose on the A row, Cell line on the B row: each variable matches wells,
    # and no well carries both.
    variables = tab.ctx.state.selection.variables
    tab.ctx.state.selection.variables = sv.VariableSet(
        variables=variables.variables,
        assignments={"dose": {"A01": "v0", "A02": "v1"},
                     "cl": {"B01": "hela", "B02": "u2os"}})
    rctl.redraw_response(tab.ctx)
    tab.ctx.state.plot.group_by_variable = "cl"

    # Read at the boundary — what the renderer hands the axes — because the
    # fixture stubs ``apply_ax_style`` to a no-op, so ``ax.get_title()`` is
    # empty here for every figure and would pass this test with the sentence
    # deleted.
    import well_viewer.core.plot_style as ps
    titles = []
    tab.monkeypatch.setattr(
        ps, "apply_ax_style", lambda _ax, title, _ylabel: titles.append(title))

    rctl.redraw_response(tab.ctx)

    assert titles, "the renderer never styled the axes"
    assert "No well holds both Cell line and Dose" in titles[-1], (
        f"the figure does not say the grouping was not applied: {titles[-1]!r}")
