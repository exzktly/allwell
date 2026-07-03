"""R6 characterization — gate values live on CellGatingState, widgets are a view.

The FluorGating / ThreshFracOn values used to be read straight off the Cell
Gating tab's QLineEdits (``edit.text()``); every consumer — the core channel
layer, the CDF plot, persistence, saved-plots capture — reached through the
widget maps. R6 makes the typed ``CellGatingState`` slice the source of truth:
the ``editingFinished`` handlers parse the edits into the slice, persistence
load writes the slice directly, and the accessors read the slice.

These tests lock the data-flow contract with *real* QLineEdits offscreen —
gate-value propagation is data flow, not paint, so a silent slice↔widget
divergence (the scientific-corruption failure mode) fails loudly here.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLineEdit

from well_viewer.core.cell_gating_state import CellGatingState


def _app(fluor=(), thresh=(), *, state=None, with_edits=True):
    """Fake app: real QLineEdits + the typed slice (+ handler stubs)."""
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        cell_gating=state if state is not None else CellGatingState(),
        plot=SimpleNamespace(),
    )
    if with_edits:
        app._cell_gating_fluor_gate_edits = {k: QLineEdit(v) for k, v in fluor}
        app._cell_gating_thresh_frac_edits = {k: QLineEdit(v) for k, v in thresh}
    # Stubs for the thresh change-handler's downstream fan-out.
    app._calls = []
    app._invalidate_stats_cache = lambda: app._calls.append("invalidate")
    app._redraw = lambda: app._calls.append("redraw")
    app._event_bus = SimpleNamespace(
        plot_options_changed=SimpleNamespace(emit=lambda s: app._calls.append("emit")))
    # The gate accessors read the slice via ctx (post-R6 pure state).
    app._ctx = SimpleNamespace(state=app._app_state, app=app)
    return app


def test_sync_parses_edits_into_slice(qtbot):
    from well_viewer.features.grouping.cell_gating_tab_view import (
        _cell_gating_sync_gate_state,
    )
    app = _app(fluor=[("gfp", "12.5"), ("ratio:g_over_m", "3")],
               thresh=[("gfp", "40"), ("ratio:g_over_m", "60.5")])
    _cell_gating_sync_gate_state(app)
    st = app._app_state.cell_gating
    assert st.fluor_gates == {"gfp": 12.5, "ratio:g_over_m": 3.0}
    assert st.thresh_frac_on == {"gfp": 40.0, "ratio:g_over_m": 60.5}


def test_sync_keeps_last_committed_on_garbage(qtbot):
    # A stray keystroke must NOT silently disable a gate: the slice keeps the
    # last-committed value (the old widget-reading accessor returned the 0.0
    # default for unparsable text, quietly turning the gate off).
    from well_viewer.features.grouping.cell_gating_tab_view import (
        _cell_gating_sync_gate_state,
        cell_gating_get_fluor_gate,
    )
    st = CellGatingState(fluor_gates={"gfp": 12.5}, thresh_frac_on={"gfp": 40.0})
    app = _app(fluor=[("gfp", "oops")], thresh=[("gfp", "nope")], state=st)
    _cell_gating_sync_gate_state(app)
    assert st.fluor_gates["gfp"] == 12.5
    assert st.thresh_frac_on["gfp"] == 40.0
    assert cell_gating_get_fluor_gate(app._ctx, "gfp") == 12.5


def test_accessors_read_slice_with_defaults_and_ratio_resolution(qtbot):
    from well_viewer.features.grouping.cell_gating_tab_view import (
        cell_gating_get_fluor_gate,
        cell_gating_get_thresh_frac_on,
    )
    st = CellGatingState(
        fluor_gates={"gfp": 7.0, "ratio:top": 2.0},
        thresh_frac_on={"gfp": 33.0, "ratio:top": 66.0},
    )
    app = _app(state=st, with_edits=False)   # no widgets at all → slice suffices
    assert cell_gating_get_fluor_gate(app._ctx, "gfp") == 7.0
    assert cell_gating_get_thresh_frac_on(app._ctx, "gfp") == 33.0
    # Bare ratio name resolves onto the ratio: key (the active_channel form).
    assert cell_gating_get_fluor_gate(app._ctx, "top") == 2.0
    assert cell_gating_get_thresh_frac_on(app._ctx, "top") == 66.0
    # Unknown channel → the historical defaults.
    assert cell_gating_get_fluor_gate(app._ctx, "mcherry") == 0.0
    assert cell_gating_get_thresh_frac_on(app._ctx, "mcherry") == 50.0


def test_load_threshold_frac_on_pushes_slice_to_widgets(qtbot):
    from well_viewer.features.grouping.cell_gating_tab_view import (
        cell_gating_load_threshold_frac_on,
    )
    st = CellGatingState(thresh_frac_on={"gfp": 33.0, "mcherry": 75.5})
    app = _app(thresh=[("gfp", "50.0"), ("mcherry", "50.0")], state=st)
    cell_gating_load_threshold_frac_on(app)
    assert app._cell_gating_thresh_frac_edits["gfp"].text() == "33.0"
    assert app._cell_gating_thresh_frac_edits["mcherry"].text() == "75.5"


def test_thresh_change_handler_syncs_then_fans_out(qtbot):
    # The editingFinished flow end-to-end: edit text → handler → slice synced,
    # stats cache invalidated, redraw + plot_options_changed emitted.
    from well_viewer.features.grouping.cell_gating_tab_view import (
        cell_gating_on_threshold_frac_on_change,
        cell_gating_get_thresh_frac_on,
    )
    app = _app(fluor=[("gfp", "0.0")], thresh=[("gfp", "50.0")])
    app._cell_gating_thresh_frac_edits["gfp"].setText("62.5")
    cell_gating_on_threshold_frac_on_change(app)
    assert app._app_state.cell_gating.thresh_frac_on["gfp"] == 62.5
    assert cell_gating_get_thresh_frac_on(app._ctx, "gfp") == 62.5
    assert app._calls == ["invalidate", "redraw", "emit"]


def test_thresh_change_handler_rejects_garbage_without_acting(qtbot):
    from well_viewer.features.grouping.cell_gating_tab_view import (
        cell_gating_on_threshold_frac_on_change,
    )
    st = CellGatingState(thresh_frac_on={"gfp": 40.0})
    app = _app(fluor=[("gfp", "0.0")], thresh=[("gfp", "garbage")], state=st)
    cell_gating_on_threshold_frac_on_change(app)
    assert st.thresh_frac_on["gfp"] == 40.0   # untouched
    assert app._calls == []                    # no fan-out on invalid input


def test_editing_finished_signal_drives_the_sync(qtbot):
    # The actual Qt wiring: editingFinished on a real QLineEdit connected the
    # way the build loop does it must land the value in the slice.
    from well_viewer.features.grouping.cell_gating_tab_view import (
        cell_gating_on_threshold_frac_on_change,
    )
    app = _app(fluor=[("gfp", "0.0")], thresh=[("gfp", "50.0")])
    edit = app._cell_gating_thresh_frac_edits["gfp"]
    edit.editingFinished.connect(
        lambda _app=app: cell_gating_on_threshold_frac_on_change(_app))
    edit.setText("71.25")
    edit.editingFinished.emit()
    assert app._app_state.cell_gating.thresh_frac_on["gfp"] == 71.25
