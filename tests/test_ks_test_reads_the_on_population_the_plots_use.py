"""The Statistics tab's KS test compares the ON population every other statistic uses.

With scatter-gate thresholding active, ON is gate membership (``Gate_On``) for
the bar plot, the line plot and the replicate statistics
(``stats_compute._thresh_on_col``). The KS test's per-cell sample went on using
``value > ThreshFracOn`` — the cutoff the tab greys out in that mode — so the
same well had two ON populations depending on the test chosen: 304 of 2,000
cells (mean 207.3) under the bars and 1,591 (mean 111.7) in the KS sample. Both
read ``data_loading.on_population_values`` now.

The expected sample is computed from the frame with numpy.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from review.core.cell_gating_state import CellGatingState
from review.data.data_loading import aggregate_with_threshold_df
from review.features.home.cell_gating.gating_compute import apply_fluor_gating
from review.features.stats import controller as stats_controller

MEAN_ON, N_ON = 1, 4   # positions in an ``aggregate_with_threshold_df`` point
GATE = {"name": "on", "x_col": "gfp_mean_intensity", "y_col": "mcherry_mean_intensity",
        "x0": 150.0, "x1": 1e6, "y0": 0.0, "y1": 1e6}
CUTOFF = 50.0          # the ThreshFracOn the tab holds


def _well(*, gate_thresholding: bool) -> pd.DataFrame:
    rng = np.random.default_rng(5)
    df = pd.DataFrame({
        "gfp_mean_intensity": rng.lognormal(np.log(80), 0.6, 2000),
        "mcherry_mean_intensity": rng.lognormal(np.log(80), 0.6, 2000),
        "area_px": 100.0, "timepoint_hours": 0.0})
    apply_fluor_gating(df, fluor_gates={}, use_scatter=False, gating_gates=[],
                       use_thresh=gate_thresholding, thresh_gates=[GATE], ratios={})
    return df


def _ks_sample(monkeypatch, df, *, gate_thresholding: bool) -> np.ndarray:
    gating = CellGatingState(use_scatter_thresh=gate_thresholding, scatter_gates=[GATE])
    ctx = SimpleNamespace(state=SimpleNamespace(
        channel=SimpleNamespace(active_val_col="gfp_mean_intensity", ratio_index={}, active_channel="gfp",
                                threshold=CUTOFF, fluor_channels=["gfp", "mcherry"]),
        cell_gating=gating, dataset=SimpleNamespace(well_paths={"A1": None})))
    monkeypatch.setattr(stats_controller, "get_rows", lambda _ctx, _label: df)
    return np.asarray(stats_controller.collect_group_per_cell_values(
        ctx, SimpleNamespace(wells=["A1"]), 0.0, val_col="gfp_mean_intensity", threshold=CUTOFF))


def test_under_gate_thresholding_the_ks_sample_is_the_cells_inside_the_gate(monkeypatch):
    df = _well(gate_thresholding=True)
    gfp = df["gfp_mean_intensity"].to_numpy()
    in_gate, above_cutoff = gfp[gfp >= 150.0], gfp[gfp > CUTOFF]
    assert (in_gate.size, above_cutoff.size) == (304, 1591)        # the two answers differ on this fixture
    sample = _ks_sample(monkeypatch, df, gate_thresholding=True)
    assert sample.size == 304
    assert np.sort(sample) == pytest.approx(np.sort(in_gate))
    # ...and it is the population the bar's mean is taken over.
    point = aggregate_with_threshold_df(df, CUTOFF, val_col="gfp_mean_intensity", on_col="Gate_On")[0]
    assert (int(point[N_ON]), float(point[MEAN_ON])) == (304, pytest.approx(float(sample.mean())))
    assert float(sample.mean()) == pytest.approx(207.3, abs=0.05)


def test_without_gate_thresholding_the_ks_sample_is_the_cells_above_the_cutoff(monkeypatch):
    df = _well(gate_thresholding=False)
    gfp = df["gfp_mean_intensity"].to_numpy()
    sample = _ks_sample(monkeypatch, df, gate_thresholding=False)
    assert sample.size == 1591
    assert np.sort(sample) == pytest.approx(np.sort(gfp[gfp > CUTOFF]))


def test_the_ks_sample_leaves_out_what_the_aggregate_leaves_out(monkeypatch):
    """Excluded cells, another timepoint's cells and cells with no area are in neither."""
    df = _well(gate_thresholding=False)
    df.loc[:99, "QC_Pass"] = 0
    df["QC_Pass"] = df["QC_Pass"].fillna(1)
    df.loc[100:199, "timepoint_hours"] = 24.0
    df.loc[200:299, "area_px"] = np.nan
    gfp = df["gfp_mean_intensity"].to_numpy()
    expected = gfp[300:][gfp[300:] > CUTOFF]
    sample = _ks_sample(monkeypatch, df, gate_thresholding=False)
    point = aggregate_with_threshold_df(df, CUTOFF, val_col="gfp_mean_intensity")[0]
    assert sample.size == expected.size == int(point[N_ON])
