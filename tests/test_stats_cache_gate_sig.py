"""Audit S4 — the stats cache key must fold in the scatter-gate on-population
state. When gate-based thresholding is active the fraction/n depends on gate
*membership*, so a gate resize/rename changes results without touching
``threshold``; keying the cache only on ``threshold`` returned stale numbers.
"""
from __future__ import annotations

import types

from well_viewer.domain.stats_compute import _gate_cache_sig

_GATE = {
    "x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0,
    "x_col": "a", "y_col": "b", "name": "Gate 1", "wells": [],
}


def _app(use_thresh, gates, choices=None):
    # Epic-cellgating: the scatter-gate on-population config (use-flag, gate
    # geometry, thresh gate selection) is typed state on ``ctx.state.cell_gating``.
    from well_viewer.core.cell_gating_state import CellGatingState
    cg = CellGatingState(
        use_scatter_thresh=use_thresh,
        scatter_gates=gates,
        thresh_choices=choices,
    )
    app = types.SimpleNamespace()
    app._app_state = types.SimpleNamespace(cell_gating=cg)
    from well_viewer.core.app_runtime import AppRuntime
    app._ctx = types.SimpleNamespace(
        app=app, state=app._app_state, runtime=AppRuntime(app))
    return app._ctx


def test_none_in_plain_threshold_mode():
    # scatter-thresh off → sig None → existing cache keys unchanged.
    assert _gate_cache_sig(_app(False, [dict(_GATE)])) is None


def test_none_when_no_gates():
    assert _gate_cache_sig(_app(True, [])) is None


def test_signature_present_when_gate_thresholding_active():
    sig = _gate_cache_sig(_app(True, [dict(_GATE)]))
    assert sig is not None and sig[0] == "Gate_On"


def test_signature_changes_when_gate_resized():
    a = _gate_cache_sig(_app(True, [dict(_GATE)]))
    resized = dict(_GATE)
    resized["x1"] = 5.0                     # drag-resize, same name
    b = _gate_cache_sig(_app(True, [resized]))
    assert a != b, "a gate resize must produce a different cache key"


def test_signature_changes_when_gate_renamed():
    a = _gate_cache_sig(_app(True, [dict(_GATE)]))
    renamed = dict(_GATE)
    renamed["name"] = "Gate 2"
    b = _gate_cache_sig(_app(True, [renamed]))
    assert a != b
