"""Integration test for the multivariate feature-matrix collection.

Exercises ``features.multivariate.collect`` with a lightweight fake ``app``
(the same shape the scatter collect is exercised with): synthetic per-well
DataFrames, gating columns, and a couple of feature columns. No Qt — collect and
the compute engine are both Qt-free.
"""

from __future__ import annotations

import types

import numpy as np
import pandas as pd

from well_viewer.features.multivariate import collect as mv_collect
from well_viewer.domain import multivariate_compute as mvc


def _well_df(seed, n=60):
    rng = np.random.default_rng(seed)
    gfp = rng.normal(1000, 200, n)
    return pd.DataFrame({
        "timepoint_hours": np.repeat([0.0, 6.0], n // 2),
        "area_px": rng.integers(50, 300, n).astype(float),
        "Included": np.ones(n, dtype=int),
        "gfp_mean_intensity": gfp,
        "mcherry_mean_intensity": 0.5 * gfp + rng.normal(0, 20, n),  # correlated
        "shape_solidity": rng.uniform(0.7, 1.0, n),
    })


def _fake_app(frames):
    channel = types.SimpleNamespace(ratio_index={}, fluor_channels=["gfp", "mcherry"])
    # Seed the row cache so the ctx-native get_rows (A8) finds these frames.
    dataset = types.SimpleNamespace(well_paths=dict.fromkeys(frames), data_dir=None,
                                    cache=dict(frames))
    selection = types.SimpleNamespace(selected_wells=set(frames))

    class _AppState:
        def __init__(self):
            self.channel = channel
            self.dataset = dataset
            self.selection = selection

        def feature_slice(self, name):
            return self._mv if name == "multivariate" else None

    app_state = _AppState()

    app = types.SimpleNamespace(
        _app_state=app_state,
        _get_rows=lambda label: frames.get(label),
        _rep_sets_active=lambda: [],
        _rank_color_well=lambda label: "#4C78A8",
        _parse_rc=lambda label: (label[0], label[1:]),
    )
    from well_viewer.core.app_runtime import AppRuntime
    app._ctx = types.SimpleNamespace(app=app, state=app_state, runtime=AppRuntime(app))
    return app


def test_collect_builds_matrix_over_selected_wells():
    frames = {"A01": _well_df(1), "A02": _well_df(2)}
    app = _fake_app(frames)
    cols = ["gfp_mean_intensity", "mcherry_mean_intensity"]
    data = mv_collect.collect_feature_matrix(app._ctx, feature_cols=cols)
    assert data["X"].shape == (120, 2)          # 60 cells x 2 wells
    assert data["n_cells"] == 120
    assert set(data["well"].tolist()) == {"A01", "A02"}
    assert set(data["group"].tolist()) == {"A01", "A02"}
    # The two columns were built correlated → correlation should be strong.
    corr = mvc.correlation_matrix(data["X"])
    assert corr[0, 1] > 0.8


def test_collect_respects_timepoint_filter():
    frames = {"A01": _well_df(3)}
    app = _fake_app(frames)
    data = mv_collect.collect_feature_matrix(
        app._ctx, feature_cols=["gfp_mean_intensity"], timepoints=[6.0])
    assert data["n_cells"] == 30                # only the t=6 half
    assert np.allclose(data["timepoint"], 6.0)


def test_collect_drops_nonfinite_rows_and_counts_them():
    df = _well_df(4)
    df.loc[df.index[:5], "gfp_mean_intensity"] = np.nan
    app = _fake_app({"A01": df})
    data = mv_collect.collect_feature_matrix(app._ctx, feature_cols=["gfp_mean_intensity"])
    assert data["n_cells"] == 55
    assert data["n_dropped_nan"] == 5


def test_collect_honours_included_gate():
    df = _well_df(5)
    df.loc[df.index[:10], "Included"] = 0
    app = _fake_app({"A01": df})
    data = mv_collect.collect_feature_matrix(app._ctx, feature_cols=["gfp_mean_intensity"])
    assert data["n_cells"] == 50


def test_available_feature_columns_discovers_families():
    frames = {"A01": _well_df(6)}
    app = _fake_app(frames)
    fams = mv_collect.available_feature_columns(app._ctx)
    assert "gfp_mean_intensity" in fams.get("Intensity", [])
    assert "mcherry_mean_intensity" in fams.get("Intensity", [])
    assert "shape_solidity" in fams.get("Shape", [])


def test_empty_selection_returns_empty_matrix():
    app = _fake_app({})
    data = mv_collect.collect_feature_matrix(app._ctx, feature_cols=["gfp_mean_intensity"])
    assert data["X"].shape == (0, 1)
    assert data["n_cells"] == 0


def test_column_display_label_formats_families():
    frames = {"A01": _well_df(7)}
    app = _fake_app(frames)
    assert mv_collect.column_display_label(app._ctx, "gfp_mean_intensity") == "GFP Mean Intensity"
    # shape label comes from the catalog
    assert "solidity" in mv_collect.column_display_label(app._ctx, "shape_solidity").lower()
