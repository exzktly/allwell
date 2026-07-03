"""QC layer (post-run debris gating) — pure verdict + chokepoint composition.

Qt-free; needs pandas. Exercises the gating math, pipeline_info seeding, the
manual-AND-QC composition at the inclusion chokepoint, and the controller's
cache projection against a lightweight fake app.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from well_viewer.data.data_loading import df_included_mask
from well_viewer.features.qc.params import QCParams, compute_qc_pass
from well_viewer.features.qc import controller as qc


def _df(area, solidity=None, included=None):
    d = {"area_px": area}
    if solidity is not None:
        d["shape_solidity"] = solidity
    if included is not None:
        d["Included"] = included
    return pd.DataFrame(d)


# ── verdict ───────────────────────────────────────────────────────────────────
def test_disabled_passes_everything():
    df = _df([1, 5, 1000])
    res = compute_qc_pass(df, QCParams(enabled=False, min_area=10))
    assert list(res) == [1, 1, 1]


def test_min_and_max_area_gate():
    df = _df([5, 50, 500])
    res = compute_qc_pass(df, QCParams(enabled=True, min_area=10, max_area=200))
    assert list(res) == [0, 1, 0]  # 5 too small, 500 too big


def test_max_region_area_gate_rejects_shattered_debris():
    # qc_region_area = area of the bright region each cell sits in; the debris
    # fragments share a large region (144) while the real nucleus is small (25).
    df = pd.DataFrame({"area_px": [25, 9, 9, 9, 9],
                       "qc_region_area": [25, 144, 144, 144, 144]})
    res = compute_qc_pass(df, QCParams(enabled=True, max_region_area=100))
    assert list(res) == [1, 0, 0, 0, 0]  # nucleus kept; 4 debris fragments dropped


def test_region_gate_missing_column_passes():
    df = pd.DataFrame({"area_px": [25, 9, 9]})  # no qc_region_area
    res = compute_qc_pass(df, QCParams(enabled=True, max_region_area=100))
    assert list(res) == [1, 1, 1]


def test_region_area_round_trips():
    p = QCParams(enabled=True, max_region_area=500)
    assert QCParams.from_dict(p.to_dict()).max_region_area == 500
    assert QCParams.from_pipeline_info(
        {"qc_options": {"remove_debris": True, "max_region_area": 500}}
    ).max_region_area == 500
    assert p.is_active


def test_min_solidity_gate_and_missing_metric_passes():
    # solidity present for some semantics; NaN solidity must pass (can't measure)
    df = _df([100, 100, 100], solidity=[0.5, 0.95, float("nan")])
    res = compute_qc_pass(df, QCParams(enabled=True, min_solidity=0.9))
    assert list(res) == [0, 1, 1]


def test_prefers_shape_area_then_area_px():
    df = pd.DataFrame({"shape_area": [5], "area_px": [500]})
    # shape_area (5) is read first → fails min_area=10
    assert list(compute_qc_pass(df, QCParams(enabled=True, min_area=10))) == [0]


def test_zero_bounds_normalize_to_none():
    p = QCParams(enabled=True).with_updates(min_area=0, max_area="", min_solidity=-1)
    assert p.min_area is None and p.max_area is None and p.min_solidity is None
    assert not p.is_active  # enabled but no bound → inactive


# ── seeding ─────────────────────────────────────────────────────────────────
def test_from_pipeline_info_seeds_from_qc_options():
    info = {"qc_options": {"remove_debris": True, "min_area": 20,
                           "max_area": 0, "min_solidity": 0.8}}
    p = QCParams.from_pipeline_info(info)
    assert p.enabled and p.min_area == 20 and p.max_area is None and p.min_solidity == 0.8


def test_from_pipeline_info_empty():
    assert QCParams.from_pipeline_info(None) == QCParams()


# ── chokepoint composition: manual-keep AND passes-QC ─────────────────────────
def test_df_included_mask_composes_included_and_qc():
    df = pd.DataFrame({"Included": [1, 1, 0, 0], "QC_Pass": [1, 0, 1, 0]})
    assert list(df_included_mask(df)) == [True, False, False, False]


def test_df_included_mask_without_qc_column_is_unchanged():
    df = pd.DataFrame({"Included": [1, 0, 1]})
    assert list(df_included_mask(df)) == [True, False, True]


def test_df_included_mask_qc_only_when_included_absent():
    df = pd.DataFrame({"QC_Pass": [1, 0, 1]})
    assert list(df_included_mask(df)) == [True, False, True]


# ── controller projection ─────────────────────────────────────────────────────
def _fake_app(cache, pipeline_info=None, params=None):
    from well_viewer.core.qc_state import QcState
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(cache=cache, pipeline_info=pipeline_info),
        qc=QcState(params=params))
    # The QC projectors take ``ctx`` (R1); a minimal ctx over the same state.
    app._ctx = SimpleNamespace(state=app._app_state, app=app)
    return app


def test_apply_qc_to_cache_writes_qc_pass_column():
    cache = {"A01": _df([5, 100], solidity=[0.99, 0.99])}
    app = _fake_app(cache, params=QCParams(enabled=True, min_area=10))
    qc.apply_qc_to_cache(app._ctx, "A01")
    assert "QC_Pass" in cache["A01"].columns
    assert list(cache["A01"]["QC_Pass"]) == [0, 1]


def test_get_qc_params_lazy_seeds_from_pipeline_info():
    app = _fake_app({}, pipeline_info={"qc_options": {"remove_debris": True, "min_area": 15}})
    p = qc.get_qc_params(app._ctx)
    assert p.enabled and p.min_area == 15
    # cached on the typed QC state afterwards
    assert app._app_state.qc.params is p


def test_qc_excluded_count_preview_params():
    cache = {"A01": _df([5, 50, 500])}
    app = _fake_app(cache, params=QCParams())  # committed: disabled
    n_excl, n_tot = qc.qc_excluded_count(app, params=QCParams(enabled=True, min_area=10, max_area=200))
    assert (n_excl, n_tot) == (2, 3)


# ── circularity gate (shape_circularity, always emitted by the pipeline) ───────
def test_min_circularity_gate_flags_jagged_cells():
    df = pd.DataFrame({"area_px": [100, 100, 100],
                       "shape_circularity": [0.95, 0.40, 0.10]})
    res = compute_qc_pass(df, QCParams(enabled=True, min_circularity=0.5))
    assert list(res) == [1, 0, 0]   # round passes; jagged/blobby fail


def test_max_circularity_gate():
    df = pd.DataFrame({"area_px": [100, 100], "shape_circularity": [0.99, 0.50]})
    res = compute_qc_pass(df, QCParams(enabled=True, max_circularity=0.8))
    assert list(res) == [0, 1]


def test_circularity_gate_missing_column_passes():
    df = pd.DataFrame({"area_px": [100, 100]})   # legacy data, no shape_circularity
    res = compute_qc_pass(df, QCParams(enabled=True, min_circularity=0.5))
    assert list(res) == [1, 1]


def test_circularity_nan_passes():
    df = pd.DataFrame({"area_px": [100, 100], "shape_circularity": [float("nan"), 0.2]})
    res = compute_qc_pass(df, QCParams(enabled=True, min_circularity=0.5))
    assert list(res) == [1, 0]   # unmeasured passes; measured-low fails


def test_circularity_round_trips_through_dict():
    p = QCParams(enabled=True, min_circularity=0.5, max_circularity=0.95)
    q = QCParams.from_dict(p.to_dict())
    assert q.min_circularity == 0.5 and q.max_circularity == 0.95
    assert q.is_active


def test_qc_gate_row_exposes_circularity_inputs(qtbot):
    """The Segmentation-tab QC gate row has min/max circularity inputs that
    seed from params and refresh on external change."""
    from PySide6.QtWidgets import QWidget

    from well_viewer.features.qc.tab_view import (
        build_qc_gate_controls, qc_gate_refresh_widgets,
    )

    from well_viewer.core.qc_state import QcState
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(data_dir=None, cache={}, pipeline_info={}),
        qc=QcState(params=QCParams(enabled=True, min_circularity=0.5, max_circularity=0.95)))
    # qc_gate_refresh_widgets reads the params through get_qc_params(ctx).
    app._ctx = SimpleNamespace(state=app._app_state, app=app)

    parent = QWidget()
    qtbot.addWidget(parent)
    row = build_qc_gate_controls(app, parent)
    qtbot.addWidget(row)

    w = app._qc_gate_widgets
    assert "min_circularity" in w and "max_circularity" in w
    assert w["min_circularity"].text() == "0.5"
    assert w["max_circularity"].text() == "0.95"

    app._app_state.qc.params = QCParams(enabled=True, min_circularity=0.7)
    qc_gate_refresh_widgets(app)
    assert w["min_circularity"].text() == "0.7"
    assert w["max_circularity"].text() == ""   # unset → blank
