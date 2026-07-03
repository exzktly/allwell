"""Regression: a persisted Cell Gating fluor-gate applies on a COLD open.

On a fresh app load the Cell Gating sub-tab is built before channels are
detected; recalculate_threshold then rebuilds the per-channel gate rows
(fresh 0.0) AFTER the persisted values were loaded, wiping them — so the saved
gate showed 0 and never filtered until the user re-entered it. The values are
now re-applied onto the rebuilt rows.
"""
from __future__ import annotations

import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


@pytest.mark.slow
def test_fluor_gate_applies_on_cold_open(qtbot, mini_dataset):
    from well_viewer.runtime_app import WellViewerApp
    from well_viewer.data.data_loading import df_included_mask
    import well_viewer.features.grouping.cell_gating_tab_view as cg

    app1 = WellViewerApp()
    app1._load_path(mini_dataset)
    app1._notebook.setCurrentByName("Sample Definitions")
    well = next(iter(app1._app_state.dataset.well_paths))
    ch = app1._app_state.channel.active_channel
    base = int(df_included_mask(app1._get_rows(well)).sum())

    app1._cell_gating_fluor_gate_edits[ch].setText("130")
    cg.cell_gating_on_gating_change(app1._ctx)
    if app1._cell_gating_worker:
        app1._cell_gating_worker.wait()
    gated = int(df_included_mask(app1._get_rows(well)).sum())
    assert gated < base, "gate should filter in the first session"

    # Cold open: a brand-new app loading the same dir applies the saved gate
    # without the user re-toggling.
    app2 = WellViewerApp()
    app2._load_path(mini_dataset)
    if getattr(app2, "_cell_gating_worker", None):
        app2._cell_gating_worker.wait()
    assert float(app2._cell_gating_fluor_gate_edits[ch].text()) == 130.0
    assert int(df_included_mask(app2._get_rows(well)).sum()) == gated
