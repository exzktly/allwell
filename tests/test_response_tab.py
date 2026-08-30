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
                        lambda ctx, rset, t, thr, sem: (10.0, 1.0, 0.5, 0.1))
    monkeypatch.setattr(ps, "apply_ax_style", lambda *a, **k: None)
    monkeypatch.setattr(ps, "apply_export_style_to_current", lambda *a, **k: None)

    from PySide6.QtWidgets import QPushButton

    combo = LabeledComboBox("X")
    qtbot.addWidget(combo)
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
    from well_viewer.core.export_prefs_state import ExportPrefsState
    from well_viewer.features.response.state import ResponseState

    slices = {"response": ResponseState()}
    statuses = []
    ctx = SimpleNamespace(
        # ``_response_fig`` too: ``figure_pref_key`` looks the figure up by its
        # registry name, and without it the prefs get an unstable key and warn.
        widgets=_Registry(_response_x_cb=combo, _response_ax=ax,
                          _response_fig=fig, _response_canvas=canvas,
                          _response_tp_button=tp_button),
        notify=SimpleNamespace(set_status=statuses.append),
        state=SimpleNamespace(
            dataset=SimpleNamespace(well_paths={}),
            selection=SimpleNamespace(selections=[], selected_wells=set(),
                                      variables=sv.VariableSet()),
            plot=SimpleNamespace(group_by_variable="", bar_pool_replicates=True,
                                 use_sem=True, focus_timepoint=24.0,
                                 line_order_rsets=[], line_order_wells=[]),
            channel=SimpleNamespace(active_val_col="gfp_mean_intensity",
                                    active_channel="gfp"),
            feature_slice=slices.get,
            # The real slice, not a stub: ``figure_prefs`` seeds it lazily and
            # distinguishes "never set" from "set to off", which is the whole
            # property the log-x tests are about.
            export_prefs=ExportPrefsState(),
        ),
        runtime=SimpleNamespace(barplot=SimpleNamespace(current_keys=lambda: [])),
    )
    return SimpleNamespace(ctx=ctx, combo=combo, ax=ax, statuses=statuses,
                           monkeypatch=monkeypatch,
                           w=ctx.widgets.get_or_none)


def _load(tab, variable, assignments, groups, timepoints=(24.0,)):
    """Point the ctx at a dataset, and say which timepoints it holds.

    ``get_all_timepoints`` walks the real row cache, so it is stubbed on the
    module the controller reads it from — the picker seeds itself from that
    list, and without it every curve would be drawn at no timepoint at all.
    """
    import well_viewer.data.data_loading as dl
    tab.monkeypatch.setattr(dl, "get_all_timepoints",
                            lambda _ctx: list(timepoints))
    wells = sorted({w for ws in (g[1] for g in groups) for w in ws})
    tab.ctx.state.dataset.well_paths = {w: f"/{w}.csv" for w in wells}
    tab.ctx.state.selection.variables = sv.VariableSet(
        variables=(variable,), assignments={variable.id: assignments})
    tab.ctx.state.selection.selections = [
        {"id": f"g{i}", "name": n, "wells": w} for i, (n, w) in enumerate(groups)]
    tab.ctx.runtime.barplot.current_keys = lambda: [g[0] for g in groups]


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

def test_an_omitted_group_reaches_the_status_line(tab):
    variable = _variable(pairs=(("1", 1.0),))
    _load(tab, variable, {"A01": "v0"},
          [("ok", ["A01"]), ("short", ["A02"])])
    rctl.redraw_response(tab.ctx)

    assert any("omitted" in s for s in tab.statuses), (
        "a group was dropped from the plot and nothing said so"
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
    assert tab.w("_response_tp_button").text() == "Timepoints: 1 selected"


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
    assert tab.w("_response_tp_button").text() == "Timepoints: 0 selected"


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
    assert any("Home → Variables" in s for s in tab.statuses), tab.statuses
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
