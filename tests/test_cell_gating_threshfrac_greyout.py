"""When the fraction-on scatter-gate mode is on, the ThreshFracOn edits grey out
(the gate replaces them) — mirroring the FluorGating greying under scatter-gate
inclusion."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def test_threshfracon_fields_grey_out_with_fraction_on_gate(loaded_app):
    from well_viewer.features.grouping import cell_gating_tab_view as cg

    app = loaded_app
    assert app._notebook.setCurrentByName("Sample Definitions")
    edits = getattr(app, "_cell_gating_thresh_frac_edits", {})
    assert edits, "expected ThreshFracOn edits"

    # Default: threshold mode → editable.
    app._app_state.cell_gating.use_scatter_thresh = False
    cg._cell_gating_apply_scatter_gate_mode(app._ctx)
    assert all(e.isEnabled() for e in edits.values())

    # Fraction-on gate on → the gate replaces the threshold → fields disabled.
    app._app_state.cell_gating.use_scatter_thresh = True
    cg._cell_gating_apply_scatter_gate_mode(app._ctx)
    assert all(not e.isEnabled() for e in edits.values())
