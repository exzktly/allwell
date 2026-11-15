"""Scatter-gate inclusion replaces the intensity floors for every consumer, not only for the gating pass.

In that mode the Cell Gating tab greys the per-channel floors out and its
"Included for analysis" count ignores them. The aggregates read their floors
from ``get_all_fluor_gates`` and went on applying the stored value, so the
count the user was shown (723 of 1,000 here) was not the population any plot
used (371). The floors in force are decided once, in
``domain.gating_state.fluor_gate_in_force``; these tests hold the count and the
aggregates to the same number, computed here independently with numpy.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from review.core.cell_gating_state import CellGatingState
from review.core.channel_state_controller import get_all_fluor_gates, get_fluor_gate, stored_fluor_gates
from review.data.data_loading import _all_fluor_values_filtered, aggregate_with_threshold_df
from review.features.home.cell_gating.gating_compute import apply_fluor_gating, inclusion_counts

N_TOTAL = 5   # ``aggregate_with_threshold_df`` point: (tp, mean, spread, frac_on, n_on, n_total, …)
GATE = {"name": "g", "x_col": "gfp_mean_intensity", "y_col": "mcherry_mean_intensity",
        "x0": 0.0, "x1": 200.0, "y0": 50.0, "y1": 200.0}


def _cells() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "gfp_mean_intensity": rng.uniform(0, 200, 1000),
        "mcherry_mean_intensity": rng.uniform(0, 200, 1000),
        "area_px": 100.0, "timepoint_hours": 0.0,
    })


def _ctx(*, use_scatter: bool):
    gating = CellGatingState(fluor_gates={"gfp": 100.0, "mcherry": 0.0}, use_scatter_gates=use_scatter)
    return SimpleNamespace(state=SimpleNamespace(
        cell_gating=gating, channel=SimpleNamespace(fluor_channels=["gfp", "mcherry"])))


def _gate_and_count(ctx, df, gates) -> tuple[int, int, int]:
    """(the tab's count, the aggregate's population, the distribution's population) after one pass."""
    floors = get_all_fluor_gates(ctx)
    apply_fluor_gating(df, fluor_gates=floors, use_scatter=ctx.state.cell_gating.use_scatter_gates,
                       gating_gates=gates, use_thresh=False, thresh_gates=[], ratios={})
    points = aggregate_with_threshold_df(df, threshold=-1e9, val_col="mcherry_mean_intensity", fluor_gates=floors)
    values = _all_fluor_values_filtered(df, "mcherry_mean_intensity", floors)
    return inclusion_counts([df])[0], int(points[0][N_TOTAL]), int(values.size)


def test_the_aggregates_use_the_population_the_tab_counts_under_a_scatter_gate():
    df = _cells()
    in_gate = int((df["mcherry_mean_intensity"] >= 50.0).sum())
    above_floor_too = int(((df["mcherry_mean_intensity"] >= 50.0) & (df["gfp_mean_intensity"] > 100.0)).sum())
    assert (in_gate, above_floor_too) == (723, 371)   # the fixture separates the right answer from the wrong one
    assert _gate_and_count(_ctx(use_scatter=True), df, [GATE]) == (723, 723, 723)


def test_scatter_gate_mode_with_no_gate_chosen_restricts_nothing_anywhere():
    assert _gate_and_count(_ctx(use_scatter=True), _cells(), []) == (1000, 1000, 1000)


def test_intensity_mode_still_applies_the_floor_everywhere():
    df = _cells()
    above = int((df["gfp_mean_intensity"] > 100.0).sum())
    assert _gate_and_count(_ctx(use_scatter=False), df, []) == (above, above, above)
    assert 0 < above < 1000


def test_the_per_axis_floor_is_off_in_scatter_gate_mode_and_the_stored_floor_is_kept_for_saving():
    on, off = _ctx(use_scatter=True), _ctx(use_scatter=False)
    assert get_fluor_gate(on, "GFP") == 0.0
    assert get_fluor_gate(off, "GFP") == 100.0
    assert get_all_fluor_gates(on) == {"gfp": 0.0, "mcherry": 0.0}
    assert stored_fluor_gates(on) == {"gfp": 100.0, "mcherry": 0.0}
