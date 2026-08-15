"""The repeating text columns are stored as categories, and nothing notices.

Profiling a loaded 537,393-row plate: 32 numeric columns came to 131 MiB and
the six text ones to 195 MiB — of which ``experiment`` and ``channel`` held one
distinct value each and cost 30 MiB apiece, and the most varied, ``filename``,
held 1,248. As categories the frame is 135 MiB. That is steady-state memory in
the row cache, held for every well of every open dataset, not a transient.

The saving is the easy part. Categorical dtype has three ways to change an
answer, and each of them was found by running the real consumers rather than by
reading them:

1. ``fillna("")`` **raises** on a Categorical whose categories lack ``""`` —
   it validates the fill value whether or not anything needs filling. The
   FOV-integrity check does this on every load, so a naive ``astype("category")``
   crashes the loader outright.
2. ``groupby`` on a Categorical emits one group per *category*, not per observed
   value. A category list wider than the data — which a filtered subset always
   has — would fabricate per-field statistics: 16 groups where there are 2,
   14 of them NaN.
3. ``pd.concat`` of columns whose categories differ falls back to ``object``,
   so pooled frames lose the type.

The tests below pin all three. The last one is fine — object is what pooling
produced before — and it measures *cheaper* than the old path, because the
category objects are shared rather than rebuilt.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from well_viewer.data.data_loading import (
    _STRING_COLS, aggregate_with_threshold_df, load_well_csv,
)
from well_viewer.domain.fov_integrity import check_fov_labels

_TEXT = sorted(_STRING_COLS)


def _write(tmp_path, n=400, n_fov=8, unique_filenames=False):
    rng = np.random.default_rng(0)
    fov = np.array([str(i) for i in rng.integers(1, n_fov + 1, n)])
    tp = rng.choice(["0h", "24h", "48h"], n)
    names = ([f"cell_{i}.tif" for i in range(n)] if unique_filenames
             else [f"exp_GFP_A01_F{f}_{t}.tif" for f, t in zip(fov, tp)])
    df = pd.DataFrame({
        "filename": names, "experiment": "exp", "channel": "GFP", "well": "A01",
        "fov": fov, "timepoint": tp,
        "timepoint_hours": np.where(tp == "0h", 0.0,
                                    np.where(tp == "24h", 24.0, 48.0)),
        "nucleus_id": np.arange(n), "area_px": rng.random(n) * 100 + 50,
        "gfp_mean_intensity": rng.random(n) * 1000, "Included": 1,
    })
    path = tmp_path / "measurements_A01.csv"
    df.to_csv(path, index=False)
    return path


def _as_object(df):
    """The same frame with the text columns back as plain strings."""
    out = df.copy()
    for c in _TEXT:
        if c in out.columns:
            out[c] = out[c].astype(str)
    return out


# ── the storage ──────────────────────────────────────────────────────────────

def test_the_repeating_text_columns_come_back_as_categories(tmp_path):
    df = load_well_csv(_write(tmp_path))
    for col in _TEXT:
        assert isinstance(df[col].dtype, pd.CategoricalDtype), col


def test_every_category_list_contains_the_empty_string(tmp_path):
    """The crash case. ``fillna("")`` validates its argument against the
    categories whether or not any value needs filling."""
    df = load_well_csv(_write(tmp_path))
    for col in _TEXT:
        assert "" in set(df[col].cat.categories), col
        df[col].fillna("")  # must not raise


def test_a_column_that_does_not_repeat_stays_a_plain_string(tmp_path):
    """Codes plus strings costs more than strings. Above the share threshold
    the column is left alone."""
    df = load_well_csv(_write(tmp_path, unique_filenames=True))
    assert df["filename"].dtype == object
    assert isinstance(df["well"].dtype, pd.CategoricalDtype)


def test_the_values_themselves_are_unchanged(tmp_path):
    df = load_well_csv(_write(tmp_path))
    raw = pd.read_csv(_write(tmp_path))
    for col in _TEXT:
        assert df[col].astype(str).tolist() == raw[col].astype(str).tolist()


# ── the consumers give the same answers ──────────────────────────────────────

@pytest.mark.parametrize("per_fov", [False, True])
def test_aggregation_is_identical_either_way(tmp_path, per_fov):
    """Hazard 2. The per-field branch groups on ``fov``; a category list wider
    than the data would emit a group per unused category and quietly fabricate
    per-field statistics."""
    df = load_well_csv(_write(tmp_path))
    kw = dict(threshold=500.0, use_sem=False, per_fov_spread=per_fov,
              val_col="gfp_mean_intensity", fluor_gates={}, ratios={},
              on_col=None)
    got = aggregate_with_threshold_df(df, **kw)
    want = aggregate_with_threshold_df(_as_object(df), **kw)
    assert len(got) == len(want)
    for a, b in zip(got, want):
        for x, y in zip(a, b):
            if isinstance(x, float) and np.isnan(x):
                assert isinstance(y, float) and np.isnan(y)
            else:
                assert x == y


def test_the_fov_integrity_verdict_is_identical(tmp_path):
    """Hazard 1 lives here: this is the caller that does ``fillna("")`` on
    ``fov`` and ``timepoint`` on every load."""
    df = load_well_csv(_write(tmp_path))
    assert check_fov_labels(df) == check_fov_labels(_as_object(df))


def test_pooling_two_wells_keeps_the_values(tmp_path):
    """Hazard 3. Differing categories cannot survive a concat, so the pooled
    frame is ``object`` — the status quo. What must not change is the data."""
    a = load_well_csv(_write(tmp_path))
    b = a.copy()
    b["well"] = pd.Categorical(["A02"] * len(b), categories=["A02", ""])
    pooled = pd.concat([a, b], ignore_index=True)
    assert pooled["well"].astype(str).tolist() == (
        ["A01"] * len(a) + ["A02"] * len(b))
    assert len(pooled) == len(a) + len(b)


def test_an_empty_frame_does_not_raise(tmp_path):
    path = tmp_path / "measurements_A01.csv"
    path.write_text("filename,well,fov,timepoint,area_px\n")
    assert load_well_csv(path).empty
