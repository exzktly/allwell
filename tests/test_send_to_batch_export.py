"""S5 — "Add to Batch" jumps from a plot card to Batch Export.

The batch panels read the live plot spec (channel/property/threshold/SEM/
fold-change) off app state, so "capture the current plot" reduces to opening
the Batch Export tab with the chart-type matching the card the user clicked.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def test_send_to_batch_export_navigates_and_sets_mode(loaded_app):
    from well_viewer.core.ui_helpers import send_plot_to_batch_export

    for mode in ("bar", "scatter_cells", "scatter_agg", "line"):
        send_plot_to_batch_export(loaded_app, mode)
        assert loaded_app._notebook.currentName() == "Batch Export"
        assert loaded_app._ctx.runtime.batch_export.inline_state["mode"] == mode


def test_unknown_mode_falls_back_to_line(loaded_app):
    from well_viewer.core.ui_helpers import send_plot_to_batch_export

    send_plot_to_batch_export(loaded_app, "nonsense")
    assert loaded_app._notebook.currentName() == "Batch Export"
    assert loaded_app._ctx.runtime.batch_export.inline_state["mode"] == "line"
