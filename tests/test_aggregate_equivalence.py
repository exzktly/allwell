"""The aggregation's timepoint resolution, pinned against a corpus of real shapes.

``aggregate_with_threshold_df`` resolves each row's timepoint from the numeric
``timepoint_hours`` column, falling back to the ``timepoint`` *string* column for
rows whose numeric value is missing. That fallback is two full-column passes —
``_ordinal_timepoints_df`` and ``timepoint_text`` — and on a plate whose numeric
column is complete, which is the ordinary case, neither result is ever consulted.

Skipping those passes is a large saving and a silent-wrong-number risk in equal
measure, so this module pins what the fallback actually produces first. Every
case here is a shape a real plate can arrive in, not a shape that was convenient
to construct:

* a complete numeric column (the ordinary plate);
* a missing numeric column with parseable labels (``0h`` / ``24h``);
* a missing numeric column with labels nothing can parse, which is the only
  route to the ordinal fallback;
* a frame mixing finite and missing numeric values;
* a categorical ``timepoint`` whose categories carry surrounding whitespace,
  which is why ``text_columns.as_text`` strips at all;
* a categorical ``fov`` that does not contain the literal ``"1"``, the case that
  makes ``fillna`` raise on a categorical;
* an empty frame, and a frame with no ``timepoint_hours`` column at all.

**The case that decides the optimisation** is ``test_excluded_row_with_an_
unresolvable_timepoint_does_not_change_the_result``: a row carrying a missing
numeric timepoint that inclusion drops anyway. Such a row cannot reach the
output — ``final`` masks it out — so the string fallback is dead work for it.
A fast path gated on *every* row being finite would be defeated by one such row;
gated on the *included* rows it is not. Real plates carry these: the pipeline
writes ``nan`` for any filename whose timepoint token it cannot parse.

The cross-well ordinal instability the two-well case documents is pre-existing
and deliberately not fixed here — see that test's own docstring.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from well_viewer.data.data_loading import aggregate_with_threshold_df

VAL_COL = "gfp_mean_intensity"


def _frame(rows: list[dict]) -> pd.DataFrame:
    """A measurement frame with the columns the aggregator requires.

    ``area_px`` is mandatory (the aggregator returns ``[]`` without it) and
    ``Included`` drives the inclusion chokepoint, so both are defaulted here and
    each case overrides only what it is about.
    """
    df = pd.DataFrame(rows)
    if "area_px" not in df.columns:
        df["area_px"] = 200.0
    if "Included" not in df.columns:
        df["Included"] = 1
    return df


def _agg(df: pd.DataFrame, **kw):
    return aggregate_with_threshold_df(df, threshold=0.0, val_col=VAL_COL, **kw)


def _timepoints(points) -> list[float]:
    return [p[0] for p in points]


def _means(points) -> list[float]:
    return [p[1] for p in points]


# ── The ordinary plate: a complete numeric column ────────────────────────────

def test_complete_numeric_timepoints_resolve_from_the_numeric_column():
    df = _frame([
        {"timepoint_hours": 0.0, "timepoint": "0h", VAL_COL: 10.0},
        {"timepoint_hours": 0.0, "timepoint": "0h", VAL_COL: 20.0},
        {"timepoint_hours": 24.0, "timepoint": "24h", VAL_COL: 30.0},
    ])
    pts = _agg(df)
    assert _timepoints(pts) == [0.0, 24.0]
    assert _means(pts) == [15.0, 30.0]


# ── The string fallback ──────────────────────────────────────────────────────

def test_missing_numeric_falls_back_to_parseable_string_labels():
    """No numeric column at all: every timepoint comes from the label."""
    df = _frame([
        {"timepoint": "0h", VAL_COL: 10.0},
        {"timepoint": "24h", VAL_COL: 30.0},
    ])
    pts = _agg(df)
    assert _timepoints(pts) == [0.0, 24.0]
    assert _means(pts) == [10.0, 30.0]


def test_unparseable_labels_fall_back_to_sorted_ordinals():
    """The only route into ``_ordinal_timepoints_df``.

    Labels nothing can parse are numbered by their sorted order within the
    frame, so ``early`` < ``late`` becomes 0.0 < 1.0.
    """
    df = _frame([
        {"timepoint": "early", VAL_COL: 10.0},
        {"timepoint": "late", VAL_COL: 30.0},
    ])
    pts = _agg(df)
    assert _timepoints(pts) == [0.0, 1.0]
    assert _means(pts) == [10.0, 30.0]


def test_a_frame_mixing_finite_and_missing_numeric_values_resolves_both_ways():
    """Half the rows carry a usable number, half need the label."""
    df = _frame([
        {"timepoint_hours": 0.0, "timepoint": "0h", VAL_COL: 10.0},
        {"timepoint_hours": np.nan, "timepoint": "24h", VAL_COL: 30.0},
    ])
    pts = _agg(df)
    assert _timepoints(pts) == [0.0, 24.0]
    assert _means(pts) == [10.0, 30.0]


def test_blank_labels_on_missing_numeric_rows_read_as_timepoint_zero():
    df = _frame([
        {"timepoint_hours": np.nan, "timepoint": "", VAL_COL: 10.0},
    ])
    pts = _agg(df)
    assert _timepoints(pts) == [0.0]


# ── The case the fast path is gated on ───────────────────────────────────────

def test_excluded_row_with_an_unresolvable_timepoint_does_not_change_the_result():
    """A dropped row's unresolvable timepoint is dead work, not a difference.

    This is the case that separates a fast path gated on *every* row's numeric
    timepoint being finite from one gated on the *included* rows'. The excluded
    row here carries a label nothing can parse, so the whole-frame test says
    "fall back to strings" while the included-rows test says "no need" — and
    they must produce the same output, because ``final`` masks the row out
    either way.

    Real plates carry such rows: ``process_microscopy`` writes ``nan`` for a
    filename whose timepoint token it cannot parse, and a QC or crop verdict can
    exclude that row independently.
    """
    df = _frame([
        {"timepoint_hours": 0.0, "timepoint": "0h", VAL_COL: 10.0, "Included": 1},
        {"timepoint_hours": 0.0, "timepoint": "0h", VAL_COL: 20.0, "Included": 1},
        {"timepoint_hours": np.nan, "timepoint": "junk", VAL_COL: 99.0, "Included": 0},
    ])
    pts = _agg(df)
    assert _timepoints(pts) == [0.0]
    assert _means(pts) == [15.0], "the excluded row must not reach the mean"


def test_an_included_row_with_an_unresolvable_timepoint_still_resolves():
    """The mirror of the case above: here the fallback genuinely is needed."""
    df = _frame([
        {"timepoint_hours": 0.0, "timepoint": "0h", VAL_COL: 10.0, "Included": 1},
        {"timepoint_hours": np.nan, "timepoint": "junk", VAL_COL: 99.0, "Included": 1},
    ])
    pts = _agg(df)
    # "junk" is the only unparseable label, so it takes ordinal 0.0 — which
    # collides with the real 0h. Both rows therefore pool into one point.
    assert _timepoints(pts) == [0.0]
    assert _means(pts) == [pytest.approx(54.5)]


# ── Categorical columns ──────────────────────────────────────────────────────

def test_categorical_timepoint_with_padded_categories_is_stripped():
    """Why ``as_text`` strips: the categories themselves can carry whitespace."""
    df = _frame([
        {"timepoint_hours": np.nan, VAL_COL: 10.0},
        {"timepoint_hours": np.nan, VAL_COL: 30.0},
    ])
    df["timepoint"] = pd.Categorical([" 0h ", "24h "])
    pts = _agg(df)
    assert _timepoints(pts) == [0.0, 24.0]


def test_categorical_fov_without_the_literal_one_survives_per_fov_spread():
    """``fillna`` on such a column raises; ``fov_text`` is why this works."""
    df = _frame([
        {"timepoint_hours": 0.0, "timepoint": "0h", VAL_COL: 10.0, "nucleus_id": 1},
        {"timepoint_hours": 0.0, "timepoint": "0h", VAL_COL: 20.0, "nucleus_id": 2},
        {"timepoint_hours": 0.0, "timepoint": "0h", VAL_COL: 30.0, "nucleus_id": 3},
        {"timepoint_hours": 0.0, "timepoint": "0h", VAL_COL: 40.0, "nucleus_id": 4},
    ])
    df["fov"] = pd.Categorical(["2", "2", "3", "3"])
    pts = _agg(df, per_fov_spread=True)
    assert _timepoints(pts) == [0.0]
    assert _means(pts) == [25.0]
    # Two fields of two cells: means 15 and 35, so the per-FOV spread is finite.
    assert np.isfinite(pts[0][2]), "per-FOV spread should be computed, not NaN"


# ── Degenerate frames ────────────────────────────────────────────────────────

def test_an_empty_frame_aggregates_to_nothing():
    assert _agg(pd.DataFrame()) == []


def test_a_frame_with_no_area_column_aggregates_to_nothing():
    df = pd.DataFrame({"timepoint_hours": [0.0], VAL_COL: [10.0], "Included": [1]})
    assert _agg(df) == []


def test_a_frame_with_no_timepoint_column_at_all_reads_as_timepoint_zero():
    """Neither a numeric nor a string timepoint still yields a point, at t=0.

    ``text_column`` answers a full column of its default for a column that is
    absent, so every row carries the blank label — and a blank label is defined
    as timepoint zero. A frame with no timepoint information is therefore one
    unlabelled timepoint, not an empty result. Pinned because the fast path must
    not turn it into one: with no numeric column ``tp_num`` is entirely NaN, so
    the included-rows test is False and the string fallback still runs.
    """
    df = _frame([{VAL_COL: 10.0}])
    pts = _agg(df)
    assert _timepoints(pts) == [0.0]
    assert _means(pts) == [10.0]


def test_every_row_excluded_aggregates_to_nothing():
    df = _frame([
        {"timepoint_hours": 0.0, "timepoint": "0h", VAL_COL: 10.0, "Included": 0},
    ])
    assert _agg(df) == []


# ── The known cross-well defect, documented rather than fixed ────────────────

def test_ordinal_timepoints_disagree_between_wells_of_one_plate():
    """A pre-existing defect this branch does not change, pinned so it is visible.

    ``_ordinal_timepoints_df`` numbers the unparseable labels present *in the
    frame it is given*. Two wells of one plate are separate frames, so a label
    that is second in one well and only in the other lands on a different
    number — and every collector unions the wells by numeric timepoint, so the
    two disagree about which point is which.

    This is not introduced or fixed here; it is asserted so that a green
    equivalence suite cannot be read as saying the ordinal path is sound. Fixing
    it means numbering the labels per *dataset* rather than per frame, which is
    a behaviour change well outside a performance branch.
    """
    well_a = _frame([
        {"timepoint": "early", VAL_COL: 10.0},
        {"timepoint": "late", VAL_COL: 30.0},
    ])
    well_b = _frame([
        {"timepoint": "late", VAL_COL: 50.0},
    ])
    pts_a = _agg(well_a)
    pts_b = _agg(well_b)
    assert _timepoints(pts_a) == [0.0, 1.0]
    assert _timepoints(pts_b) == [0.0]
    # "late" is t=1.0 in well A and t=0.0 in well B — the same label, two
    # different points, on one plate.
    late_in_a = pts_a[1][0]
    late_in_b = pts_b[0][0]
    assert late_in_a != late_in_b
