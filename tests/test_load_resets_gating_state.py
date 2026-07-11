"""Audit X1 — a dataset swap must clear the prior dataset's scatter-gate /
cell-gating / per-cell-override state, or (when the new dataset has no
cell_gating sidecar, so the loaders early-return) the stale gates silently
filter the new dataset's cells and redefine fraction-on.
"""
from __future__ import annotations

import types

from well_viewer.core.app_runtime import AppRuntime
from well_viewer.core.cell_gating_state import CellGatingState
from well_viewer.core.load_controller import _reset_gating_state


def test_reset_clears_all_leaky_gating_attrs():
    # Epic-cellgating: the scatter-gate geometry + use-flags + per-layer gate
    # selections are typed state on ``ctx.state.cell_gating`` (joining the R6
    # gate values), so the swap-reset clears the slice, not loose app attrs.
    # The per-cell include-override map is runtime scratch, reset through
    # ``ctx.runtime.review.included_overrides``.
    cg = CellGatingState(
        scatter_gates=[{"name": "G1", "x0": 0}],
        use_scatter_gates=True,
        use_scatter_thresh=True,
        gating_choices={"G1"},
        thresh_choices={"G1"},
    )
    rt = AppRuntime()
    rt.review.included_overrides = {"A01/0/1/5": False}
    app = types.SimpleNamespace(
        runtime=rt,
        _app_state=types.SimpleNamespace(
            cell_gating=cg,
            heatmap=types.SimpleNamespace(active_layout_name="old-layout"),
        ),
    )
    app.window = app  # the mock doubles as its own ctx (ctx.window -> app)

    _reset_gating_state(app)

    assert cg.scatter_gates == []
    assert rt.review.included_overrides == {}
    assert cg.use_scatter_gates is False
    assert cg.use_scatter_thresh is False
    assert cg.gating_choices is None
    assert cg.thresh_choices is None
    assert app._app_state.heatmap.active_layout_name is None
