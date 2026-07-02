"""Audit X1 — a dataset swap must clear the prior dataset's scatter-gate /
cell-gating / per-cell-override state, or (when the new dataset has no
cell_gating sidecar, so the loaders early-return) the stale gates silently
filter the new dataset's cells and redefine fraction-on.
"""
from __future__ import annotations

import types

from well_viewer.core.load_controller import _reset_gating_state


def test_reset_clears_all_leaky_gating_attrs():
    app = types.SimpleNamespace(
        _scatter_gates=[{"name": "G1", "x0": 0}],
        _review_included_overrides={"A01/0/1/5": False},
        _cell_gating_use_scatter_gates=True,
        _cell_gating_use_scatter_thresh=True,
        _cell_gating_gating_choices={"G1"},
        _cell_gating_thresh_choices={"G1"},
        _active_heatmap_layout_name="old-layout",
    )

    _reset_gating_state(app)

    assert app._scatter_gates == []
    assert app._review_included_overrides == {}
    assert app._cell_gating_use_scatter_gates is False
    assert app._cell_gating_use_scatter_thresh is False
    assert app._cell_gating_gating_choices is None
    assert app._cell_gating_thresh_choices is None
    assert app._active_heatmap_layout_name is None
