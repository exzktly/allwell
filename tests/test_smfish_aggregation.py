"""Regression tests for the smFISH-count plotting fix.

Pins the contract for both halves of the bug:

* ``aggregate_with_threshold_df`` with a sub-minimum ``threshold`` (e.g.
  ``-1.0``) must include zero-spot cells in the mean and total counts —
  the function contract shape metrics rely on (and that smFISH counts used
  before they switched to honouring the channel's ``ThreshFracOn``; the
  controllers now feed that cutoff instead, but the function itself is
  unchanged: ``value > threshold`` defines the "on" population). The old
  default ``threshold=0`` silently dropped every cell whose count was
  zero, which is real signal in spot-count data.

* ``resolve_value_series`` returns a NaN-filled Series (right-indexed)
  when the requested column is missing, after first attempting the
  case-folded fallback that recovers mixed-case headers from older
  cached frames.
"""

from __future__ import annotations

import logging

import pandas as pd

from review.data import data_loading as _dl
from review.data.data_loading import (
    aggregate_with_threshold_df,
    resolve_value_series,
)


def _frame(values, *, col="w3cy5_smfish_count", tp_hours=24.0):
    n = len(values)
    return pd.DataFrame({
        "Included": [1] * n,
        "area_px": [200.0] * n,
        "timepoint_hours": [tp_hours] * n,
        "timepoint": [f"{int(tp_hours):02d}h"] * n,
        col: values,
    })


def test_smfish_all_zero_counts_contribute_to_mean():
    df = _frame([0, 0, 0, 0, 0, 0])
    pts = aggregate_with_threshold_df(
        df,
        threshold=-1.0,
        val_col="w3cy5_smfish_count",
    )
    assert len(pts) == 1
    t, mean, sd, frac, n_above, n_total, *_ = pts[0]
    assert t == 24.0
    assert mean == 0.0
    assert n_above == 6
    assert n_total == 6


def test_smfish_mixed_counts_include_zeros():
    df = _frame([0, 3, 5, 0, 2, 1])
    pts = aggregate_with_threshold_df(
        df,
        threshold=-1.0,
        val_col="w3cy5_smfish_count",
    )
    assert len(pts) == 1
    _, mean, _, _, n_above, n_total, *_ = pts[0]
    assert n_total == 6
    assert n_above == 6
    assert mean == 11 / 6


def test_smfish_old_threshold_zero_drops_zero_cells():
    """Pins the bug-class: threshold=0 would drop zero-spot cells. The
    fix relies on -1.0 being plumbed everywhere the metric is
    'smfish_count'; if any path regresses to 0 the resulting mean is
    artificially elevated and n_above drops."""
    df = _frame([0, 3, 5, 0, 2, 1])
    pts = aggregate_with_threshold_df(
        df,
        threshold=0.0,
        val_col="w3cy5_smfish_count",
    )
    _, mean, _, _, n_above, n_total, *_ = pts[0]
    assert n_above == 4
    assert n_total == 6
    assert mean == (3 + 5 + 2 + 1) / 4


def test_resolve_value_series_missing_column_returns_nan(caplog):
    df = _frame([1, 2, 3])
    # The diagnostic dedupes on (id(df), key) — clear so this test can fire
    # the WARN regardless of test-order interactions with other suites that
    # may have already emitted (and dedup-suppressed) the same key.
    _dl._missing_col_warned.clear()
    # status_view sets `propagate = False` on the "review" logger when a
    # ReviewApp is constructed (so GUI log records do not double-print on
    # the root handler). caplog attaches to the root, so after any test that
    # builds an app this WARN never reaches it. Re-enable propagation for the
    # duration of this test.
    _wv_logger = logging.getLogger("review")
    _prev_propagate = _wv_logger.propagate
    _wv_logger.propagate = True
    try:
        caplog.set_level(logging.WARNING, logger="review")
        out = resolve_value_series(df, "w4gfp_smfish_count", {})
        assert len(out) == 3
        assert out.isna().all()
        # rec.getMessage() interpolates %-args; rec.message is only set after
        # a formatter touches the record and is unreliable across test orderings.
        assert any(
            "resolve_value_series" in rec.getMessage()
            and "w4gfp_smfish_count" in rec.getMessage()
            for rec in caplog.records
        )
    finally:
        _wv_logger.propagate = _prev_propagate


def test_resolve_value_series_case_folded_fallback():
    df = pd.DataFrame({
        "Included": [1, 1, 1],
        "W3Cy5_smfish_count": [0, 4, 7],  # mixed-case header (pre-fix cache)
    })
    out = resolve_value_series(df, "w3cy5_smfish_count", {})
    assert list(out.to_numpy()) == [0.0, 4.0, 7.0]


def test_smfish_aggregation_with_missing_column_returns_no_points():
    """The column-missing path through aggregate_with_threshold_df must
    not crash and must not emit fabricated points."""
    df = _frame([1, 2, 3])
    pts = aggregate_with_threshold_df(
        df,
        threshold=-1.0,
        val_col="w4gfp_smfish_count",  # not present
    )
    assert pts == []
