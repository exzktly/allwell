"""The Nanowells page in the real app (tracker V9, V8a): built from the rail, drawn, saved and restored.

Full ``ReviewApp`` constructions, so the slow lane (``conftest.py`` marks ``viewer_app`` users ``slow``);
every other page test is in ``test_nanowell_page.py`` against a minimal shell.
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PySide6")

from tests.test_nanowell_page import _write_nanowell_dataset  # noqa: E402


@pytest.fixture
def nanowell_app(viewer_app, tmp_path):
    viewer_app._load_path(_write_nanowell_dataset(tmp_path))
    return viewer_app


def _open(app, qtbot):
    from review.persistence import view_state

    ctx = app._ctx
    ctx.state.selection.selected_wells = {"A01", "B01"}
    view_state._activate_tab(ctx, "Nanowells")
    st = ctx.state.feature_slice("nanowell")
    qtbot.waitUntil(lambda: st.metrics is not None and not st.computing, timeout=30000)
    return ctx, st


def test_the_page_builds_from_the_rail_and_draws_the_dataset(nanowell_app, qtbot, caplog):
    caplog.set_level(logging.ERROR, logger="review")
    ctx, st = _open(nanowell_app, qtbot)
    assert ctx.widgets.get_or_none("_nw_canvas") is not None
    assert not [r for r in caplog.records if "Nanowells" in r.getMessage()]   # the builder did not raise
    texts = [t.get_text() for t in ctx.widgets.get("_nw_fig").texts]
    assert any(t.startswith("Nanowell × timepoint values used 14 of 16") for t in texts)


def test_every_view_draws_in_the_real_app_and_a_saved_plot_restores_the_view_and_well(nanowell_app, qtbot, caplog):
    """Each view through the real app's figure chrome (Properties styling, the card), and the one-well views'
    choices through a Saved Plot round trip."""
    from review.features.nanowell import controller as nwc
    from review.features.saved_plots.controller import get_store, restore_saved_plot, save_plot_from_card
    from review.persistence import view_state

    caplog.set_level(logging.WARNING, logger="review")
    ctx, st = _open(nanowell_app, qtbot)
    fig = ctx.widgets.get("_nw_fig")
    # This dataset's grid records have no pitch or montage: measured, the nanowells are drawn and the footer says
    # there is no montage; on the lattice there is nowhere to place them, and the figure says that instead.
    footers = {("array", "qc"): "nw-array-footer", ("array", "map"): "nw-array-missing",
               ("trajectories", None): "nw-traj-footer", ("position", None): "nw-position-footer"}
    for (view, preset), gid in footers.items():
        nwc.set_view(ctx, view)
        if preset:
            nwc.apply_array_preset(ctx, preset)
        if preset == "qc":
            qtbot.waitUntil(lambda: ("A01", "t_c") in st.montages, timeout=30000)
        found = [t for t in list(fig.texts) + [t for ax in fig.axes for t in ax.texts] if t.get_gid() == gid]
        assert found, view
    assert not [r for r in caplog.records if "Nanowells" in r.getMessage() and r.levelno >= logging.ERROR]
    nwc.set_view(ctx, "array")
    nwc.set_well(ctx, "B01")
    nwc.set_timepoint(ctx, "t_a")
    assert save_plot_from_card(ctx, "Nanowells", b"\x89PNG", "Nanowells").startswith("Saved")
    view_state._activate_tab(ctx, "Home")
    st.view, st.well, st.timepoint = "distribution", "", ""
    st.layers.positions = "measured"
    restore_saved_plot(ctx, get_store(ctx).records[-1].id)
    assert (st.view, st.well, st.timepoint, st.layers.positions) == ("array", "B01", "t_a", "lattice")


def test_a_saved_plot_restores_the_metric_the_axis_and_the_selected_nanowells(nanowell_app, qtbot):
    from review.features.saved_plots.controller import get_store, restore_saved_plot, save_plot_from_card
    from review.persistence import view_state

    ctx, st = _open(nanowell_app, qtbot)
    st.metric, st.axis, st.selected_sites = "confluence_fc", "wells", [["A01", 1, 0], ["B01", 0, 0]]
    assert save_plot_from_card(ctx, "Nanowells", b"\x89PNG", "Nanowells").startswith("Saved")
    view_state._activate_tab(ctx, "Home")
    st.metric, st.axis, st.selected_sites = "n", "time", []
    restore_saved_plot(ctx, get_store(ctx).records[-1].id)
    assert (st.metric, st.axis) == ("confluence_fc", "wells")
    assert st.selected_sites == [["A01", 1, 0], ["B01", 0, 0]]
    qtbot.waitUntil(lambda: not st.computing, timeout=30000)
    assert ctx.widgets.get("_nw_fig").axes[0].get_ylabel() == "Confluence fold change"      # and redrawn


def test_each_dataset_keeps_its_own_page_choices(viewer_app, qtbot, tmp_path):
    """Saved with dataset A, absent from B: B opens on the defaults and A gets its own back (D9)."""
    from review.persistence import view_state

    a = _write_nanowell_dataset(tmp_path / "a")
    b = _write_nanowell_dataset(tmp_path / "b")
    viewer_app._load_path(a)
    ctx, st = _open(viewer_app, qtbot)
    combo = ctx.widgets.get("_nw_metric_cb")
    combo.setCurrentIndex(combo.findData("c"))
    st.selected_sites = [["A01", 1, 0]]
    ctx.persistence.save("nanowell")
    viewer_app._load_path(b)
    view_state._activate_tab(ctx, "Nanowells")
    assert (st.metric, st.selected_sites) == ("n", [])
    viewer_app._load_path(a)
    view_state._activate_tab(ctx, "Nanowells")
    assert (st.metric, st.selected_sites) == ("c", [["A01", 1, 0]])


def test_group_by_and_colour_by_are_one_setting_on_the_page_and_the_plotting_band(nanowell_app, qtbot):
    """The page's Group by and Decorate are the plotting band's settings, in the real app where both surfaces exist.

    ``test_nanowell_page_variables.py`` drives the page in a minimal shell, which has no plotting band — so the half
    that keeps the two surfaces agreeing (a change on the page refilling the band's combos, a change on the band
    reaching the page's) only runs here. Values are assigned against the well order (A01 holds the variable's
    *second* value), so inking or grouping by position draws a different figure from the right one.
    """
    from matplotlib.colors import to_hex

    from review.core.group_by_controller import refresh_group_by_combo
    from review.domain import sample_variables as sv
    from review.domain import variable_series as vs
    from review.features.nanowell import controller as nwc
    from review.persistence import view_state

    ctx = nanowell_app._ctx
    line = sv.Variable(id="line", name="Cell line", kind=sv.TEXT, unit="",
                       values=(sv.VariableValue("h", "HeLa", None), sv.VariableValue("u", "U2OS", None)))
    ctx.state.selection.variables = sv.VariableSet(variables=(line,),
                                                   assignments={"line": {"A01": "u", "A02": "h", "B01": "u"}})
    refresh_group_by_combo(ctx)                 # what the Variables tab calls after an edit
    # Shown, as a user's window is: the page reads the wells a change brings in only while it is on screen
    # (``nw_on_selection_changed``), and the fixture's window is never shown otherwise.
    nanowell_app.show()
    qtbot.waitExposed(nanowell_app)
    ctx, st = _open(nanowell_app, qtbot)
    assert ctx.widgets.get("_nw_page_visible")()
    page_cb, band_cb = ctx.widgets.get("_nw_groupby_cb"), ctx.widgets.get("_plotting_groupby_cb")
    assert ctx.widgets.get("_nw_groupby_grp").isVisibleTo(ctx.window)

    # The page's combo writes the one setting, and the band's combo shows it.
    page_cb.setCurrentIndex(page_cb.findData("line"))
    assert ctx.state.plot.group_by_variable == "line" and band_cb.currentData() == "line"
    # Grouping pools every well holding a value, A02 included, whose table the page then reads.
    qtbot.waitUntil(lambda: nwc.page_groups(ctx) == {"HeLa": ["A02"], "U2OS": ["A01", "B01"]}, timeout=30000)

    # The band's combo changes it back, and the page's combo follows the next time the page is shown.
    band_cb.setCurrentIndex(band_cb.findData(""))
    assert ctx.state.plot.group_by_variable == ""
    view_state._activate_tab(ctx, "Bar Plots")
    view_state._activate_tab(ctx, "Nanowells")
    assert page_cb.currentData() == ""

    # Colour by on the page: the band's popover shows it, and the dots on the canvas take each well's value's ink.
    nwc.set_view(ctx, "distribution")
    nwc.set_axis(ctx, "wells")
    colour = ctx.widgets.get("_nw_colourby_cb")
    colour.setCurrentIndex(colour.findData("line"))
    assert ctx.widgets.get("_plotting_colourby_cb").currentData() == "line"
    qtbot.waitUntil(lambda: not st.computing, timeout=30000)
    ax = ctx.widgets.get("_nw_fig").axes[0]
    ticks = [t.get_text() for t in ax.get_xticklabels()]
    drawn = {ticks[int(round(c.get_offsets()[:, 0].mean()))]: {to_hex(f) for f in c.get_facecolors()}
             for c in ax.collections if c.get_gid() == "nw-dots"}
    palette = vs.colors_for(ctx.state.selection.variables, "line")
    # Both selected wells are U2OS, so each is named with its well, and both take U2OS's ink.
    assert drawn == {"U2OS (A01)": {to_hex(palette["u"])}, "U2OS (B01)": {to_hex(palette["u"])}}
