"""The per-cell scatter applies a channel's intensity floor the way every other consumer does.

Two ways it did not. A floor of 0.0 is the Cell Gating tab's "gate off" and
every aggregate skips it (``active_fluor_gates``); the scatter compared the
axis against it, so a background-subtracted channel lost each cell at or below
zero — 352 of 1,000 here — and the gate percentages printed on the figure were
denominated by what was left (24.7% where the gating pass counts 51.2%). And a
floor is a minimum on the channel's *mean* intensity, yet it was compared
against whichever of the channel's metrics was on the axis, so a floor of 10
was applied to total intensities a hundred times larger.

``metric_registry.fluor_floor_judges_column`` is the one rule; the expected
counts below are computed from the frame with numpy, not from the app.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from review.domain.gates import compute_gate_inclusion
from review.domain.metric_registry import fluor_floor_judges_column
from review.features.home.cell_gating.gating_compute import apply_fluor_gating, inclusion_counts
from review.features.scatter._cells import well_cloud

MASK, UNGATED = 4, 5   # positions in ``well_cloud``'s tuple


def _dim_channel(floors: dict) -> pd.DataFrame:
    """A background-subtracted dim GFP — a third of the cells at or below zero — gated by *floors*."""
    rng = np.random.default_rng(1)
    mean = rng.normal(5, 12, 1000)
    # The total is deliberately NOT ordered like the mean, and is below 10 for
    # some cells whose mean is above it, so "total > floor" and "mean > floor"
    # pick different cells in both directions.
    df = pd.DataFrame({
        "gfp_mean_intensity": mean, "gfp_total_intensity": (mean - 15.0) * rng.uniform(20, 400, 1000),
        "mcherry_mean_intensity": rng.normal(300, 50, 1000), "area_px": 100.0, "timepoint_hours": 0.0})
    apply_fluor_gating(df, fluor_gates=floors, use_scatter=False, gating_gates=[],
                       use_thresh=False, thresh_gates=[], ratios={})
    return df


def _cloud(df, col_x, floor_x, gates=None):
    return well_cloud(df, col_x=col_x, col_y="mcherry_mean_intensity", target_tps=[0.0],
                      fluor_gate_x=floor_x, fluor_gate_y=0.0, gates=gates, ratios={})


def test_a_floor_of_zero_is_off_so_cells_at_or_below_zero_are_drawn():
    df = _dim_channel({"gfp": 0.0, "mcherry": 0.0})
    assert int((df["gfp_mean_intensity"] <= 0).sum()) == 352      # the cells the defect dropped
    assert inclusion_counts([df])[0] == 1000
    assert int(_cloud(df, "gfp_mean_intensity", 0.0)[MASK].sum()) == 1000


def test_a_gate_percentage_is_denominated_by_the_cells_the_gating_pass_counts():
    df = _dim_channel({"gfp": 0.0, "mcherry": 0.0})
    gate = [{"name": "dim", "x_col": "gfp_mean_intensity", "y_col": "mcherry_mean_intensity",
             "x0": -100.0, "x1": 5.0, "y0": 0.0, "y1": 1000.0}]
    inside = compute_gate_inclusion(df, gate, {}, nanowell_values=None)
    cloud = _cloud(df, "gfp_mean_intensity", 0.0, gates=gate)
    assert (int(inside.sum()), 1000) == (512, 1000)
    assert (int(cloud[MASK].sum()), int(cloud[UNGATED].sum())) == (512, 1000)


def test_a_mean_intensity_floor_is_not_compared_against_the_total_on_the_axis():
    df = _dim_channel({"gfp": 10.0, "mcherry": 0.0})
    above_mean = int((df["gfp_mean_intensity"] > 10.0).sum())
    above_both = int(((df["gfp_mean_intensity"] > 10.0) & (df["gfp_total_intensity"] > 10.0)).sum())
    above_total = int((df["gfp_total_intensity"] > 10.0).sum())
    assert above_mean == 317 and above_both < above_mean and above_total != above_mean
    assert inclusion_counts([df])[0] == above_mean
    # The cells the tab counts, whichever of the channel's metrics is drawn.
    assert int(_cloud(df, "gfp_total_intensity", 10.0)[MASK].sum()) == above_mean
    assert int(_cloud(df, "gfp_mean_intensity", 10.0)[MASK].sum()) == above_mean


def test_a_negative_floor_on_the_mean_is_still_a_floor():
    df = _dim_channel({"gfp": -5.0, "mcherry": 0.0})
    expected = int((df["gfp_mean_intensity"] > -5.0).sum())
    assert 0 < expected < 1000
    assert int(_cloud(df, "gfp_mean_intensity", -5.0)[MASK].sum()) == expected


def test_the_rule_names_the_columns_a_floor_judges():
    assert fluor_floor_judges_column("gfp_mean_intensity", 10.0)
    assert not fluor_floor_judges_column("gfp_mean_intensity", 0.0)
    assert not fluor_floor_judges_column("gfp_total_intensity", 10.0)
    assert not fluor_floor_judges_column("gfp_smfish_count", 10.0)
    assert not fluor_floor_judges_column("ratio:gfp/mcherry", 10.0)
