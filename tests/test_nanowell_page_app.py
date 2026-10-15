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
    assert any(t.startswith("Nanowells used 14 of 16") for t in texts)


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
