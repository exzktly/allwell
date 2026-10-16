"""A well evicted after a gating pass comes back gated (tracker M2).

Intensity and scatter-gate gating were written onto the cached frames once, by
``GatingWorker``, and never again — sound while the row cache held every well for
the session. Bounded (``review/data/row_cache.py``), a gated well can be evicted
and read back from disk, and it came back with the CSV's ``Included`` and no
``Gate_Pass`` / ``Gate_On``: every plot then counted its ungated cells.

The gating projector (``gating_compute.apply_gating_to_cache``) re-applies the
configuration the last pass recorded. These tests hold it through the real
projector registry, the order against curation (an override must survive the
gate), and — once — through the real app and the real worker.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from review.core.cell_gating_state import CellGatingState
from review.core.dataset_state import DatasetState
from review.data.data_loading import df_included_mask, get_rows
from review.data.row_cache import frame_bytes
from review.data.well_repository import WellRepository
from review.features.registry import build_feature_registry

_ROWS = 100


def _write_well(tmp_path, label):
    path = tmp_path / f"{label}.csv"
    pd.DataFrame({
        "well": [label] * _ROWS,
        "fov": ["1"] * _ROWS,
        "timepoint": ["0"] * _ROWS,
        "nucleus_id": [str(i + 1) for i in range(_ROWS)],
        "gfp_mean_intensity": np.arange(1.0, _ROWS + 1.0),     # 1 … 100
    }).to_csv(path, index=False)
    return path


def _config(**over):
    config = {"fluor_gates": {"gfp": 60.0}, "use_scatter": False, "gating_gates": [],
              "use_thresh": False, "thresh_gates": [], "ratios": {}}
    config.update(over)
    return config


@pytest.fixture
def ctx(tmp_path):
    dataset = DatasetState()
    for label in ("A01", "A02"):
        dataset.well_paths[label] = _write_well(tmp_path, label)
    obj = SimpleNamespace(
        state=SimpleNamespace(
            dataset=dataset,
            cell_gating=CellGatingState(),
            review_image=SimpleNamespace(override_version=0),
            qc=SimpleNamespace(params=None),
        ),
        runtime=SimpleNamespace(review=SimpleNamespace(included_overrides={})),
    )
    obj.state.repo = WellRepository(obj)
    build_feature_registry()
    return obj


def _evict(ctx, label):
    cache = ctx.state.dataset.cache
    cache.set_budget(frame_bytes(get_rows(ctx, label)) + 1)
    for other in ctx.state.dataset.well_paths:
        if other != label:
            get_rows(ctx, other)
    assert label not in cache, "the fixture did not evict; the test measures nothing"


def test_an_evicted_well_comes_back_gated(ctx):
    ctx.state.cell_gating.applied_gating = _config()
    frame = get_rows(ctx, "A01")
    # Cells 61 … 100 pass a gate at 60: forty, not the hundred the CSV includes.
    assert int(df_included_mask(frame).sum()) == 40
    _evict(ctx, "A01")
    again = get_rows(ctx, "A01")
    assert int(df_included_mask(again).sum()) == 40
    assert list(again["Gate_Pass"].unique()) == [1] and list(again["Gate_On"].unique()) == [1]


def test_scatter_gate_columns_come_back_too(ctx):
    gate = {"name": "G", "x_col": "gfp_mean_intensity", "y_col": "gfp_mean_intensity",
            "x0": 0.0, "x1": 25.5, "y0": 0.0, "y1": 1000.0}
    ctx.state.cell_gating.applied_gating = _config(
        fluor_gates={}, use_scatter=True, gating_gates=[gate], use_thresh=True, thresh_gates=[gate])
    expected = get_rows(ctx, "A01")[["Included", "Gate_Pass", "Gate_On"]].copy()
    assert int(expected["Gate_Pass"].sum()) == 25, "the gate geometry does not select what the test assumes"
    _evict(ctx, "A01")
    pd.testing.assert_frame_equal(get_rows(ctx, "A01")[["Included", "Gate_Pass", "Gate_On"]], expected)


def test_a_manual_inclusion_survives_the_gate_on_reload(ctx):
    """Curation runs after gating, as the pass does; the other order would let the
    gate take back a cell the user had put in by hand."""
    ctx.state.cell_gating.applied_gating = _config()
    ctx.runtime.review.included_overrides = {("A01", "1", "0", "5"): "1"}
    _evict(ctx, "A01")
    again = get_rows(ctx, "A01")
    assert int(df_included_mask(again).sum()) == 41
    assert int(again.loc[again["nucleus_id"].astype(str) == "5", "Included"].iloc[0]) == 1


def test_no_pass_means_no_gating(ctx):
    frame = get_rows(ctx, "A01")
    assert int(df_included_mask(frame).sum()) == _ROWS
    assert "Gate_Pass" not in frame.columns


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("QT_QPA_PLATFORM") not in (None, "offscreen"), reason="offscreen only")
def test_the_real_pass_records_what_the_projector_reapplies(qtbot, mini_dataset):
    """Through the real app: set a gate, run the real worker, evict every well,
    read them back — each well's included count is what the pass left."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from review.runtime_app import ReviewApp
    import review.features.home.cell_gating.controller as cg

    app = ReviewApp()
    qtbot.addWidget(app)
    app._load_path(mini_dataset)
    app._notebook.setCurrentByName("Home")
    ch = app._app_state.channel.active_channel
    wells = list(app._app_state.dataset.well_paths)
    before = {w: int(df_included_mask(app._get_rows(w)).sum()) for w in wells}

    app._cell_gating_fluor_gate_edits[ch].setText("130")
    cg.cell_gating_on_gating_change(app._ctx)
    if app._ctx.runtime.cell_gating.worker:
        app._ctx.runtime.cell_gating.worker.wait()
    gated = {w: int(df_included_mask(app._get_rows(w)).sum()) for w in wells}
    assert gated != before, "the gate removed nothing, so the comparison below measures nothing"

    cache = app._app_state.dataset.cache
    cache.set_budget(1)                       # holds only the most recent well
    for w in wells:
        app._get_rows(w)
    evicted = wells[0]
    assert evicted not in cache
    assert {w: int(df_included_mask(app._get_rows(w)).sum()) for w in wells} == gated


def test_a_dataset_swap_forgets_the_previous_pass():
    """The new plate's wells must not be gated by the old plate's pass before a
    pass has run on them."""
    from review.core.load_controller import _reset_gating_state

    cg = CellGatingState(applied_gating=_config())
    state = SimpleNamespace(cell_gating=cg, feature_slice=lambda name: None)
    ctx = SimpleNamespace(window=SimpleNamespace(_app_state=state),
                          runtime=SimpleNamespace(review=SimpleNamespace(included_overrides={})))
    _reset_gating_state(ctx)
    assert cg.applied_gating is None
