"""A Properties edit restyles the plot it was made on, and no other.

Asked for: "Everywhere in the app, changes made to the properties bar should affect only the plot for which it is
edited." Typography, grid, legend, ticks, layout and the export profile used to live in one app-wide dict, so raising
the axis-label size on the line plot raised it on every plot; the Nanowells page's five views shared one figure and
so one set of settings; and a Batch Export Style Preset was written into that app-wide dict, restyling every plot in
the app.

Every case below builds its fixture so the old behaviour gives a different answer from the new one: the second plot
is read *after* the first is edited, and the value it must keep is the one it had, not "something other than the
edit".
"""

from __future__ import annotations

import pytest

from review.core.export_prefs_state import ExportPrefsState
from review.core.plot_style import (
    DEFAULT_EXPORT_STYLE_PREFS,
    figure_prefs,
    plot_style_of,
    set_pref_scope,
)
from tests.test_properties_panel_survives_a_dataset_load import _figure, _stub_app

pytestmark = pytest.mark.qt

BIG = 31                     # no default and no built-in profile uses this size, so finding it means it was written
assert BIG not in (DEFAULT_EXPORT_STYLE_PREFS["axis_label_size"], 22)


def _two_plots():
    app = _stub_app()
    line, _ = _figure()
    bar, _ = _figure()
    app._line_fig, app._bar_fig = line, bar
    return app, line, bar


def _sidebar(qtbot, monkeypatch, app, fig, name="line"):
    from review.features.export.properties_view import ExportStyleSidebar

    monkeypatch.setattr(ExportStyleSidebar, "_redraw_bound_figure", lambda self: True, raising=False)
    sb = ExportStyleSidebar(app, None, fig, None, default_name=name)
    qtbot.addWidget(sb)
    app._export_style_sidebars = {id(fig): sb}
    return sb


# ── P1: every preference is the edited plot's own ──────────────────────────
def test_a_font_size_set_in_one_plot_s_properties_leaves_another_plot_as_it_was(qtbot, monkeypatch):
    app, line, bar = _two_plots()
    before = figure_prefs(app._ctx, bar)["axis_label_size"]
    sb = _sidebar(qtbot, monkeypatch, app, line)

    sb._setters["axis_label_size"](BIG)

    assert figure_prefs(app._ctx, line)["axis_label_size"] == BIG
    assert figure_prefs(app._ctx, bar)["axis_label_size"] == before
    assert app._app_state.export_prefs.style_prefs.get("axis_label_size") != BIG


# ── P5: an export profile chosen in Properties restyles only that plot ────────
def test_a_profile_chosen_in_one_plot_s_properties_restyles_that_plot_only(qtbot, monkeypatch):
    app, line, bar = _two_plots()
    before = {k: figure_prefs(app._ctx, bar)[k] for k in ("axis_label_size", "tick_label_size", "title_size")}
    sb = _sidebar(qtbot, monkeypatch, app, line)

    sb._profile_combo.setCurrentText("Helvetica 22")

    assert figure_prefs(app._ctx, line)["tick_label_size"] == 22
    assert figure_prefs(app._ctx, line)["export_profile"] == "Helvetica 22"
    assert {k: figure_prefs(app._ctx, bar)[k] for k in before} == before
    assert figure_prefs(app._ctx, bar)["export_profile"] == DEFAULT_EXPORT_STYLE_PREFS["export_profile"]


# ── P2: a figure that shows several views keeps settings per view ─────────────
def test_a_figure_s_views_each_keep_their_own_settings_and_the_open_panel_follows_the_view(qtbot, monkeypatch):
    app = _stub_app()
    fig, _ = _figure()
    app._nw_fig = fig
    sb = _sidebar(qtbot, monkeypatch, app, fig, name="nanowell")

    sb._setters["y_lim_max"]("4")
    sb._setters["axis_label_size"](BIG)
    set_pref_scope(app._ctx, fig, "array")

    assert figure_prefs(app._ctx, fig).get("y_lim_max") in (None, "")
    assert figure_prefs(app._ctx, fig)["axis_label_size"] != BIG
    assert sb._getters["y_lim_max"]() in (None, ""), "the open panel still shows the previous view's range"
    sb._setters["y_lim_max"]("9")                                       # an edit made on the Array view

    set_pref_scope(app._ctx, fig, "")
    assert sb._getters["y_lim_max"]() == "4"
    assert figure_prefs(app._ctx, fig)["axis_label_size"] == BIG
    store = app._app_state.export_prefs.axis_prefs
    assert store["nanowell"]["y_lim_max"] == "4" and store["nanowell:array"]["y_lim_max"] == "9"


# ── P4: Batch Export draws with its source plot's style, and its preset stays its own ─────────
class _BatchStub:
    """The two Batch Export methods that decide a figure's style, off the real class."""

    from review.features.batch_export.base_panel import BatchExportPanel as _P

    _apply_export_profile_to_prefs = _P._apply_export_profile_to_prefs
    _batch_style = _P._batch_style

    def __init__(self, ctx, mode):
        from PySide6.QtWidgets import QComboBox

        self._ctx, self._mode, self._profile_overrides = ctx, mode, {}
        self._fmt_cb = QComboBox()
        self._fmt_cb.addItems(["png", "svg", "eps", "pdf"])
        self.formats = []

    def _mode_key(self):
        return self._mode

    def _on_fmt_change(self, fmt):
        self.formats.append(fmt)


def test_batch_export_draws_with_its_source_plot_s_style_not_another_plot_s(qtbot, monkeypatch):
    app, line, bar = _two_plots()
    sb = _sidebar(qtbot, monkeypatch, app, line)
    sb._setters["axis_label_size"](BIG)

    assert _BatchStub(app._ctx, "line")._batch_style()["axis_label_size"] == BIG
    assert _BatchStub(app._ctx, "bar")._batch_style()["axis_label_size"] == figure_prefs(app._ctx, bar)[
        "axis_label_size"] != BIG


def test_a_batch_style_preset_styles_the_batch_figures_and_no_plot(qtbot):
    from review.core.app_runtime import AppRuntime

    app, line, bar = _two_plots()
    app._ctx.runtime = AppRuntime(app)
    panel = _BatchStub(app._ctx, "bar")

    panel._apply_export_profile_to_prefs("Helvetica 22")
    panel._apply_export_profile_to_prefs("Print PDF")
    panel._apply_export_profile_to_prefs("Helvetica 22")

    assert panel._batch_style()["tick_label_size"] == 22 and panel.formats[-2] == "pdf"
    for fig in (line, bar):
        assert figure_prefs(app._ctx, fig)["tick_label_size"] == DEFAULT_EXPORT_STYLE_PREFS["tick_label_size"]
    assert app._app_state.export_prefs.style_prefs.get("tick_label_size") in (
        None, DEFAULT_EXPORT_STYLE_PREFS["tick_label_size"])


def test_a_style_saved_app_wide_before_the_split_still_reaches_batch_figures():
    """The legacy dict is what an untouched plot reads; a batch figure of that plot must read it too."""
    app, _line, _bar = _two_plots()
    app._app_state.export_prefs = ExportPrefsState(style_prefs={**DEFAULT_EXPORT_STYLE_PREFS, "axis_label_size": BIG})
    assert plot_style_of(app._ctx, "bar")["axis_label_size"] == BIG
    assert "y_lim_max" not in plot_style_of(app._ctx, "bar")


# ── P2 through the real Nanowells page: a range typed on Distribution is not the Trajectories' ─────────
def test_a_range_typed_on_the_nanowell_distribution_does_not_reach_the_trajectories(qtbot, tmp_path):
    from review.features.nanowell import controller as nwc
    from tests.test_nanowell_page import _loaded, _page, _write_nanowell_dataset

    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    ctx.state.selection.selected_wells = {"A01", "B01"}
    _loaded(qtbot, ctx)
    fig = ctx.widgets.get("_nw_fig")

    def main_ylim():
        return tuple(round(v, 6) for v in fig.axes[0].get_ylim())

    nwc.set_view(ctx, "trajectories")
    untouched = main_ylim()
    nwc.set_view(ctx, "distribution")
    figure_prefs(ctx, fig)["y_lim_min"], figure_prefs(ctx, fig)["y_lim_max"] = "-50", "50"
    nwc.redraw(ctx)
    assert main_ylim() == (-50.0, 50.0)                                # the range does reach its own view

    nwc.set_view(ctx, "trajectories")
    assert main_ylim() == untouched
    nwc.set_view(ctx, "distribution")
    assert main_ylim() == (-50.0, 50.0)
