"""Regression: restoring a multi-timepoint Bar selection must redraw the plot.

Reported bug: a saved view-state with several Bar timepoints checked reopened
with all the checkboxes correctly checked, but the plot showed only one
timepoint. Cause was load-order: ``_on_tab_change`` populated the timepoint menu
(``_update_bar_tp_menu``, which defaults ``tp_selections`` to a single checked
timepoint) and drew the bars *before* ``apply_pending_tab_state`` restored the
saved multi-select — and the restore didn't trigger a redraw. So the plot kept
the single-timepoint render while the checkboxes showed the restored selection.

This drives the real tab-change path on a loaded app and asserts the bar
renderer's authoritative timepoint source reflects the restored multi-select.
"""
from __future__ import annotations

import pytest

from well_viewer.features.barplot.renderer import resolve_bar_timepoints

pytestmark = pytest.mark.qt


def test_pending_bar_timepoints_survive_tab_activation(loaded_app):
    app = loaded_app
    from well_viewer.core.tab_controller import on_tab_change

    # All timepoints present in the dataset, formatted as the popup keys are.
    all_tps = sorted(app._app_state.dataset.all_timepoints_cache) or [0.0]
    tp_strs = [f"{t:.4g}" for t in all_tps]
    if len(tp_strs) < 2:
        pytest.skip("dataset has <2 timepoints; multi-select restore is trivial")

    # Land on the Bar Plots tab, then stage a saved view-state with EVERY
    # timepoint checked — exactly what view_state.restore stashes on reopening a
    # dataset before it activates the saved tab.
    assert app._notebook.setCurrentByName("Plotting")
    assert app._plotting_notebook.setCurrentByName("Bar Plots")
    app._ctx.runtime.view_state.pending = {
        "Bar Plots": {"timepoint_selections": {s: True for s in tp_strs}},
    }

    # Drive the real centre-tab dispatcher (what view_state._activate_tab triggers
    # via the notebook change on load). _last_tab_name differs so it's a real
    # transition into Bar Plots: populate menu -> draw -> restore pending -> redraw.
    app._ctx.runtime.tab.last_tab_name = "Line Graphs"
    on_tab_change(app._ctx)

    # The renderer's authoritative timepoint source must reflect the restored
    # multi-select (not the single default _update_bar_tp_menu seeds), and the
    # pending entry must have been consumed.
    resolved = {s for _f, s in resolve_bar_timepoints(app._ctx)}
    assert resolved == set(tp_strs), (resolved, tp_strs)
    assert not (app._ctx.runtime.view_state.pending or {}).get("Bar Plots")
