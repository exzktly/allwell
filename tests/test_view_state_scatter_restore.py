"""Regression: scatter axis selections must persist across a session."""
from __future__ import annotations

import pytest

from well_viewer.persistence import view_state as vs

pytestmark = pytest.mark.qt


def _selectable(cb):
    return [cb.itemText(i) for i in range(cb.count())
            if cb.itemText(i) and not cb.itemText(i).startswith("—")]


def test_scatter_axes_round_trip(loaded_app):
    app = loaded_app
    from well_viewer.core.tab_controller import on_tab_change

    assert app._notebook.setCurrentByName("Plotting")
    assert app._plotting_notebook.setCurrentByName("Scatter Plot")
    app._last_tab_name = "Line Graphs"
    on_tab_change(app)

    items = _selectable(app._scatter_ch_x_cb)
    if len(items) < 2:
        pytest.skip("dataset has <2 scatter channels")
    want_x, want_y = items[1], items[0]
    app._scatter_ch_x_cb.setCurrentText(want_x)
    app._scatter_ch_y_cb.setCurrentText(want_y)

    snap = vs.snapshot(app)
    assert snap["tabs"].get("Scatter Plot", {}).get("ch_x") == want_x, snap.get("tabs")

    # New session: reset to defaults, then restore + activate.
    app._scatter_ch_x_cb.setCurrentText(items[0])
    app._scatter_ch_y_cb.setCurrentText(items[0])
    vs.restore(app, snap)
    app._last_tab_name = "Line Graphs"
    app._notebook.setCurrentByName("Plotting")
    app._plotting_notebook.setCurrentByName("Scatter Plot")
    on_tab_change(app)

    assert app._scatter_ch_x_cb.currentText() == want_x
    assert app._scatter_ch_y_cb.currentText() == want_y


def test_scatter_metric_round_trip(loaded_app):
    app = loaded_app
    from well_viewer.core.tab_controller import on_tab_change

    assert app._notebook.setCurrentByName("Plotting")
    assert app._plotting_notebook.setCurrentByName("Scatter Plot")
    app._last_tab_name = "Line Graphs"
    on_tab_change(app)

    mx = _selectable(app._scatter_metric_x_cb)
    if len(mx) < 2:
        pytest.skip("dataset has <2 scatter metrics")
    want_mx, want_my = mx[1], mx[1]
    app._scatter_metric_x_cb.setCurrentText(want_mx)
    app._scatter_metric_y_cb.setCurrentText(want_my)

    snap = vs.snapshot(app)
    assert snap["tabs"].get("Scatter Plot", {}).get("metric_x") == want_mx, snap.get("tabs")

    app._scatter_metric_x_cb.setCurrentText(mx[0])
    app._scatter_metric_y_cb.setCurrentText(mx[0])
    vs.restore(app, snap)
    app._last_tab_name = "Line Graphs"
    app._notebook.setCurrentByName("Plotting")
    app._plotting_notebook.setCurrentByName("Scatter Plot")
    on_tab_change(app)

    assert app._scatter_metric_x_cb.currentText() == want_mx
    assert app._scatter_metric_y_cb.currentText() == want_my


def test_scatter_agg_round_trip(loaded_app):
    app = loaded_app
    from well_viewer.core.tab_controller import on_tab_change

    assert app._notebook.setCurrentByName("Plotting")
    assert app._plotting_notebook.setCurrentByName("Scatter Plot")
    app._last_tab_name = "Line Graphs"
    on_tab_change(app)

    cb = getattr(app, "_scatter_agg_ch_x_cb", None)
    if cb is None:
        pytest.skip("no aggregate scatter channel combo")
    items = _selectable(cb)
    if len(items) < 2:
        pytest.skip("dataset has <2 agg scatter channels")
    want = items[1]
    cb.setCurrentText(want)

    snap = vs.snapshot(app)

    cb.setCurrentText(items[0])
    vs.restore(app, snap)
    app._last_tab_name = "Line Graphs"
    app._notebook.setCurrentByName("Plotting")
    app._plotting_notebook.setCurrentByName("Scatter Plot")
    on_tab_change(app)

    assert app._scatter_agg_ch_x_cb.currentText() == want
