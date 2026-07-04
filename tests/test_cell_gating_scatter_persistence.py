"""Epic-cellgating characterization — the cell-gating save→load round-trip.

SCIENTIFIC-CORRUPTION-SENSITIVE: the persisted cell_gating block drives which
cells are counted (scatter-gate inclusion / fraction-on) and the per-channel
FluorGating / ThreshFracOn cutoffs. A silent divergence between what is saved and
what is re-loaded would change gated cell counts without any visible error.

This pins that ``persistence.cell_gating.save_to_pipeline_info`` →
``load_from_pipeline_info`` round-trips the FULL config **byte-identically**:
scatter gates + the two use-flags + the per-layer gate selections + the fluor /
thresh cutoffs. The config now lives on the typed ``ctx.state.cell_gating``
slice, so this exercises the real serialize/deserialize wiring off ctx.state.

The Qt fan-out at the tail of load (the status-chip quick-edit refresh) is pure
UI and is patched to a no-op; the widget mirror (``cell_gating_apply_persisted_
state``) runs for real (it is a graceful no-op with no built widgets).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from well_viewer.core.app_runtime import AppRuntime
from well_viewer.core.cell_gating_state import CellGatingState
from well_viewer.domain.gating_state import read_gating_params
from well_viewer.persistence.cell_gating import (
    load_from_pipeline_info,
    save_to_pipeline_info,
)


def _ctx(tmp_path, cg):
    """A ctx whose typed cell_gating slice is *cg*, a real (empty pipeline_info)
    data dir, and a 'tab already built' shim (empty edit maps) so the lazy-build
    seam returns immediately without any Qt tab."""
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(data_dir=tmp_path),
        channel=SimpleNamespace(
            fluor_channels=["gfp", "mcherry"],
            active_channel="gfp",
            threshold=50.0,
        ),
        cell_gating=cg,
        plot=SimpleNamespace(),
    )
    # 'Tab built' shim: ensure_cell_gating_tab_built sees these → returns True
    # without force-building a real Qt tab. Empty ⇒ the widget mirror no-ops.
    app._cell_gating_fluor_gate_edits = {}
    app._cell_gating_thresh_frac_edits = {}
    ctx = SimpleNamespace(
        app=app,
        state=app._app_state,
        runtime=AppRuntime(app),
        widgets=SimpleNamespace(get_or_none=lambda _n: None,
                                register=lambda n, w: w),
        bus=SimpleNamespace(),
        set_status=lambda _m: None,
    )
    return ctx


def _full_config():
    return CellGatingState(
        fluor_gates={"gfp": 12.5},                 # mcherry left at default 0.0
        thresh_frac_on={"gfp": 60.0},              # mcherry left at default 50.0
        scatter_gates=[
            {"x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0,
             "x_col": "gfp_mean_intensity", "y_col": "mcherry_mean_intensity",
             "name": "G1"},
            {"x0": 5.0, "y0": 6.0, "x1": 7.0, "y1": 8.0,
             "x_col": "gfp_mean_intensity", "y_col": "mcherry_mean_intensity",
             "name": "G2"},
        ],
        use_scatter_gates=True,
        use_scatter_thresh=True,
        # subsets (not the full name set) so the "collapse full selection to
        # None (=all)" pruning does NOT fire — keeps the round-trip byte-exact.
        gating_choices={"G1"},
        thresh_choices={"G2"},
    )


def test_scatter_gate_config_round_trips_byte_identical(tmp_path, monkeypatch):
    # Patch only the pure-UI status/quick-edit refresh at the tail of load.
    import well_viewer.core.channel_state_controller as csc
    monkeypatch.setattr(csc, "refresh_thresh_frac_quick_edit", lambda _ctx: None)

    (tmp_path / "pipeline_info.json").write_text(json.dumps({"channels": ["gfp"]}))

    # Save from a fully-configured slice.
    cg_a = _full_config()
    save_to_pipeline_info(_ctx(tmp_path, cg_a))
    block_after_save = read_gating_params(tmp_path)

    # Load into a *fresh* slice.
    cg_b = CellGatingState()
    ctx_b = _ctx(tmp_path, cg_b)
    assert load_from_pipeline_info(ctx_b) is True

    # The loaded slice reproduces every field.
    assert cg_b.fluor_gates == {"gfp": 12.5}
    assert cg_b.thresh_frac_on == {"gfp": 60.0}
    assert cg_b.scatter_gates == cg_a.scatter_gates
    assert cg_b.use_scatter_gates is True
    assert cg_b.use_scatter_thresh is True
    assert cg_b.gating_choices == {"G1"}
    assert cg_b.thresh_choices == {"G2"}

    # Re-saving from the loaded slice yields a byte-identical sidecar block.
    save_to_pipeline_info(ctx_b)
    block_after_reload = read_gating_params(tmp_path)
    assert block_after_reload == block_after_save
    # Spot-check the block actually carries the whole config (not silently empty).
    assert block_after_save["scatter_gates"] == cg_a.scatter_gates
    assert block_after_save["use_scatter_gates"] is True
    assert block_after_save["use_scatter_thresh"] is True
    assert sorted(block_after_save["scatter_gate_choices_gating"]) == ["G1"]
    assert sorted(block_after_save["scatter_gate_choices_thresh"]) == ["G2"]
    assert block_after_save["fluor_gates"] == {"gfp": 12.5}
    assert block_after_save["thresh_frac_on"] == {"gfp": 60.0}


def test_default_config_removes_block(tmp_path, monkeypatch):
    import well_viewer.core.channel_state_controller as csc
    monkeypatch.setattr(csc, "refresh_thresh_frac_quick_edit", lambda _ctx: None)

    # A sidecar carrying a stale block …
    (tmp_path / "pipeline_info.json").write_text(
        json.dumps({"channels": ["gfp"], "cell_gating": {"use_scatter_gates": True,
                    "scatter_gates": [{"x0": 0, "y0": 0, "x1": 1, "y1": 1}]}}))
    # … saving an all-default slice drops the block entirely (no phantom gating).
    save_to_pipeline_info(_ctx(tmp_path, CellGatingState()))
    assert read_gating_params(tmp_path) is None
    # And loading that now-clean sidecar reports "nothing applied".
    assert load_from_pipeline_info(_ctx(tmp_path, CellGatingState())) is False
