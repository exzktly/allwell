"""The threshold optimiser and the Cell Gating CDFs read the cells every plot counts.

Their population (``gating_compute._cell_gating_valid_mask``) honoured three
inclusion layers — QC, the crop and the FOV selection — of the eleven that are
independent of the fluorescence decision. A field of bright debris the user had
excluded in QC triage, flagged fields, cells deleted by hand: all stayed in the
optimiser's negative control, so here it chose a cutoff of 233.1 and reported
591 of 2,800 cells ON for a well every plot counts as 2,000 cells, where the
excluded field removed gives 144.5 and 9 of 2,000. The mask is the composed inclusion mask now, minus the fluorescence
decision's own layers.

The reference for each case is the same optimiser run on a frame from which the
excluded rows were physically removed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from review.data.data_loading import VERDICT_LAYERS, aggregate_with_threshold_df
from review.features.home.cell_gating.gating_compute import (
    VALIDITY_COLUMNS, _cell_gating_first_frame_rows, _cell_gating_valid_mask,
    _optimize_thresh_frac_on_between_wells, _valid_values_for_channel,
)

N_ON, N_TOTAL = 4, 5   # positions in an ``aggregate_with_threshold_df`` point
INDEPENDENT_LAYERS = tuple(c for c in VERDICT_LAYERS if c != "Gate_Pass")


def _well(rng, n, mu, fov="1") -> pd.DataFrame:
    return pd.DataFrame({"gfp_mean_intensity": rng.lognormal(np.log(mu), 0.35, n), "area_px": 120.0,
                         "timepoint_hours": 0.0, "fov": fov, "Included": 1})


def _controls() -> tuple[pd.DataFrame, pd.DataFrame]:
    """A positive control, and a negative one carrying a second field of 800 bright debris objects."""
    rng = np.random.default_rng(3)
    positive, negative = _well(rng, 2000, 400), _well(rng, 2000, 60)
    return positive, pd.concat([negative, _well(rng, 800, 300, fov="2")], ignore_index=True)


def _optimise(a, b):
    return _optimize_thresh_frac_on_between_wells(a, b, channel_key="gfp", min_threshold=0, max_threshold=3000)


def test_the_roster_is_every_layer_but_the_fluorescence_decision():
    assert VALIDITY_COLUMNS == set(INDEPENDENT_LAYERS)
    assert len(INDEPENDENT_LAYERS) == 11


@pytest.mark.parametrize("layer", INDEPENDENT_LAYERS)
def test_a_field_excluded_by_any_layer_does_not_set_the_threshold(layer):
    positive, negative = _controls()
    clean = _optimise(positive, negative[negative["fov"] == "1"])
    assert (clean.k_b, clean.n_b) == (9, 2000) and round(clean.threshold, 1) == 144.5

    negative[layer] = (negative["fov"] != "2").astype(int)
    result = _optimise(positive, negative)
    assert (round(result.threshold, 1), result.k_b, result.n_b) == (144.5, 9, 2000)
    # ...which is what every plot counts for that well at that cutoff.
    point = aggregate_with_threshold_df(negative, result.threshold, val_col="gfp_mean_intensity")[0]
    assert (int(point[N_ON]), int(point[N_TOTAL])) == (result.k_b, result.n_b)


def test_the_fluorescence_decision_itself_is_not_applied():
    """Filtering by the gate being set would be circular: ``Included`` and ``Gate_Pass`` are left out."""
    df = pd.DataFrame({
        "gfp_mean_intensity": np.arange(10, dtype=float), "timepoint_hours": 0.0,
        "Included":            [0, 0, 0, 0, 0, 1, 1, 1, 1, 1],
        "Gate_Pass":           [1, 1, 1, 1, 1, 1, 1, 0, 0, 0],
        "Focus_Pass":          [0, 0, 1, 1, 1, 1, 1, 1, 1, 1],
        "Field_Pass":          [1, 1, 0, 0, 1, 1, 1, 1, 1, 1],
        "Excluded_Field_Pass": [1, 1, 1, 1, 0, 0, 1, 1, 1, 1],
    })
    assert _cell_gating_valid_mask(df).tolist() == [False] * 6 + [True] * 4
    assert _valid_values_for_channel(df, "gfp").tolist() == [6.0, 7.0, 8.0, 9.0]


def _two_fields_two_timepoints() -> pd.DataFrame:
    return pd.DataFrame({
        "fov": ["1", "1", "1", "1", "2", "2", "2", "2"],
        "timepoint_hours": [0.0, 0.0, 24.0, 24.0, 0.0, 0.0, 24.0, 24.0],
        "gfp_mean_intensity": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
    })


def test_the_cdfs_first_frame_is_the_first_one_with_a_cell_the_plots_count():
    """An excluded first field would otherwise leave the CDFs empty while every plot has cells."""
    df = _two_fields_two_timepoints()
    rows, desc = _cell_gating_first_frame_rows(df)
    assert (rows["gfp_mean_intensity"].tolist(), desc) == ([1.0, 2.0], "fov=1, tp=0")

    df["Excluded_Field_Pass"] = (df["fov"] != "1").astype(int)          # the whole first field, by hand
    rows, desc = _cell_gating_first_frame_rows(df)
    assert (rows["gfp_mean_intensity"].tolist(), desc) == ([5.0, 6.0], "fov=2, tp=0")

    df = _two_fields_two_timepoints()
    df["Field_Pass"] = [0, 0, 1, 1, 1, 1, 1, 1]                         # the first field's first timepoint, flagged
    df["Curation_Pass"] = [1, 1, 0, 1, 1, 1, 1, 1]                      # and one cell of its next, deleted
    rows, desc = _cell_gating_first_frame_rows(df)
    assert (rows["gfp_mean_intensity"].tolist(), desc) == ([4.0], "fov=1, tp=24")
