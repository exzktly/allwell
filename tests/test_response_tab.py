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

    combo = LabeledComboBox("X")
    qtbot.addWidget(combo)
    # A bare ``Figure``, not ``pyplot.subplots``: pyplot would build a Qt canvas
    # and a navigation toolbar this test never shows, and tearing those down
    # after the fixture raises "Internal C++ object already deleted". The axes
    # is all the renderer touches.
    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    canvas = SimpleNamespace(draw_idle=lambda: None)

    statuses = []
    ctx = SimpleNamespace(
        widgets=_Registry(_response_x_cb=combo, _response_ax=ax,
                          _response_canvas=canvas),
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
        ),
        runtime=SimpleNamespace(barplot=SimpleNamespace(current_keys=lambda: [])),
    )
    return SimpleNamespace(ctx=ctx, combo=combo, ax=ax, statuses=statuses)


def _load(tab, variable, assignments, groups):
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


def test_the_timepoint_is_read_and_never_written(tab):
    """Writing ``focus_timepoint`` from here moves Heat Map and Distribution to
    whatever this tab happened to open on."""
    variable = _variable()
    _load(tab, variable, {"A01": "v0"}, [("a", ["A01"])])
    rctl.redraw_response(tab.ctx)

    assert tab.ctx.state.plot.focus_timepoint == 24.0
    assert rctl.timepoints_for(tab.ctx) == [24.0]


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
    assert any("pick a Number variable" in s for s in tab.statuses)
