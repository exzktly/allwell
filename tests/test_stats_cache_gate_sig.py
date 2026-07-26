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


def _app(use_thresh, gates, choices=None, *, use_gating=False, gating_choices=None):
    # Epic-cellgating: the scatter-gate on-population config (use-flag, gate
    # geometry, thresh gate selection) is typed state on ``ctx.state.cell_gating``.
    from well_viewer.core.cell_gating_state import CellGatingState
    cg = CellGatingState(
        use_scatter_thresh=use_thresh,
        scatter_gates=gates,
        thresh_choices=choices,
        use_scatter_gates=use_gating,
        gating_choices=gating_choices,
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


# ── the inclusion layer ("Use scatter gates") ───────────────────────────────
#
# The rest of the cache key folds in ``fluor_gates``, which is what makes a
# numeric FluorGating edit recompute. Toggling scatter-gate *inclusion* does not
# move those numbers, so without the flag in the key every cached statistic came
# back unchanged.

def test_signature_present_when_scatter_inclusion_is_on():
    sig = _gate_cache_sig(_app(False, [dict(_GATE)], use_gating=True))
    assert sig is not None and sig[0] == "Gate_Pass"


def test_toggling_scatter_inclusion_changes_the_key():
    off = _gate_cache_sig(_app(False, [dict(_GATE)], use_gating=False))
    on = _gate_cache_sig(_app(False, [dict(_GATE)], use_gating=True))
    assert off != on, "ticking 'Use scatter gates' must not reuse the cached stats"


def test_scatter_inclusion_with_no_gates_still_differs_from_off():
    """Mode on with nothing drawn is NOT the same population as mode off: the
    worker still sets Included=1, bypassing the per-channel intensity floors."""
    off = _gate_cache_sig(_app(False, [], use_gating=False))
    on = _gate_cache_sig(_app(False, [], use_gating=True))
    assert off is None
    assert on is not None and off != on


def test_inclusion_gate_selection_is_part_of_the_key():
    a = _gate_cache_sig(_app(False, [dict(_GATE)], use_gating=True,
                             gating_choices={"Gate 1"}))
    b = _gate_cache_sig(_app(False, [dict(_GATE)], use_gating=True,
                             gating_choices=set()))
    assert a != b


def test_the_two_layers_are_keyed_independently():
    """Each layer has its own selection; changing one must not look like the
    other. Thresholding still leads the tuple when it is active."""
    thresh_only = _gate_cache_sig(_app(True, [dict(_GATE)]))
    both = _gate_cache_sig(_app(True, [dict(_GATE)], use_gating=True))
    assert thresh_only[0] == both[0] == "Gate_On"
    assert thresh_only != both
