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
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(cache=cache, pipeline_info=pipeline_info))
    if params is not None:
        app._qc_params = params
    return app


def test_apply_qc_to_cache_writes_qc_pass_column():
    cache = {"A01": _df([5, 100], solidity=[0.99, 0.99])}
    app = _fake_app(cache, params=QCParams(enabled=True, min_area=10))
    qc.apply_qc_to_cache(app, "A01")
    assert "QC_Pass" in cache["A01"].columns
    assert list(cache["A01"]["QC_Pass"]) == [0, 1]


def test_get_qc_params_lazy_seeds_from_pipeline_info():
    app = _fake_app({}, pipeline_info={"qc_options": {"remove_debris": True, "min_area": 15}})
    p = qc.get_qc_params(app)
    assert p.enabled and p.min_area == 15
    # cached on the app afterwards
    assert app._qc_params is p


def test_qc_excluded_count_preview_params():
    cache = {"A01": _df([5, 50, 500])}
    app = _fake_app(cache, params=QCParams())  # committed: disabled
    n_excl, n_tot = qc.qc_excluded_count(app, params=QCParams(enabled=True, min_area=10, max_area=200))
    assert (n_excl, n_tot) == (2, 3)
