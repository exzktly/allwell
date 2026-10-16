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
    footers = {"array_map": "nw-map-missing", "trajectories": "nw-traj-footer", "position": "nw-position-footer",
               "array_qc": "nw-qc-missing"}                      # this dataset's grid records have no pitch or montage
    for view, gid in footers.items():
        nwc.set_view(ctx, view)
        if view == "array_qc":
            qtbot.waitUntil(lambda: ("A01", "t_c") in st.montages, timeout=30000)
        found = [t for t in list(fig.texts) + [t for ax in fig.axes for t in ax.texts] if t.get_gid() == gid]
        assert found, view
    assert not [r for r in caplog.records if "Nanowells" in r.getMessage() and r.levelno >= logging.ERROR]
    nwc.set_view(ctx, "array_map")
    nwc.set_well(ctx, "B01")
    nwc.set_timepoint(ctx, "t_a")
    assert save_plot_from_card(ctx, "Nanowells", b"\x89PNG", "Nanowells").startswith("Saved")
    view_state._activate_tab(ctx, "Home")
    st.view, st.well, st.timepoint = "distribution", "", ""
    restore_saved_plot(ctx, get_store(ctx).records[-1].id)
    assert (st.view, st.well, st.timepoint) == ("array_map", "B01", "t_a")


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
