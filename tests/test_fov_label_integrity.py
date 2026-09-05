"""A well's fields of view must be real before anything is reported per field.

The failure this pins: a well whose image filenames don't match the configured
schema gets an empty ``fov`` token on every row (``parse_filename`` fills a slot
it can't find with ``""``), and the loader maps ``""`` and ``"-1"`` onto ``"1"``
— the single-field convention. Six fields then pool under one label, and after
loading that is indistinguishable from a well that genuinely has one field.

Two things went wrong downstream, and only one of them was visible:

* the per-field spread became undefined, and an undefined spread was reported as
  ``0.0`` — which draws no error bar and exports ``0.000000``, i.e. "the
  replicates agreed exactly". The well simply appeared to have tight data.
* the "mean events per field" became the *pooled total* — six fields' events
  plotted next to correctly-labelled wells as if it were one field's mean. That
  one is not a missing number, it is a wrong number, and nothing said so.

So: detect the collapse from evidence the CSV already carries, refuse to report
per-field statistics for those wells, and never let "undefined" render as zero.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from well_viewer.data.data_loading import (
    aggregate_with_threshold_df,
    normalize_fov_series,
)
from well_viewer.domain.fov_integrity import check_fov_labels


def _rows(fov_tokens, files, n_per=20, tp="8h", seed=1):
    """One well's rows: a nuclear image per (field, timepoint), cells within it."""
    rng = np.random.default_rng(seed)
    out = []
    for fov, fname in zip(fov_tokens, files):
        for nid in range(1, n_per + 1):
            out.append({
                "fov": fov, "filename": fname, "nucleus_id": nid,
                "timepoint": tp, "timepoint_hours": 8.0,
                "area_px": 200.0, "Included": 1,
                "gfp_mean_intensity": float(rng.normal(600, 150)),
            })
    df = pd.DataFrame(out)
    df["fov"] = normalize_fov_series(df["fov"])   # the loader's normalisation
    return df


SIX_IMAGES = [f"exp_gfp_D03_{i}_8h.tif" for i in range(1, 7)]


def _healthy():
    return _rows([str(i) for i in range(1, 7)], SIX_IMAGES)


def _collapsed():
    """Six images, every row labelled field "1" — the schema missed the slot."""
    return _rows([""] * 6, SIX_IMAGES)


def _single_field():
    return _rows(["1"], ["exp_gfp_A01_1_8h.tif"])


# ── Detection ───────────────────────────────────────────────────────────────


def test_healthy_labels_are_trusted():
    assert check_fov_labels(_healthy()).trusted


def test_a_genuine_single_field_well_is_trusted():
    """The check must never fire on a real single-field dataset — that is the
    case the ""/-1 → "1" convention exists to serve."""
    assert check_fov_labels(_single_field()).trusted


def test_collapsed_labels_are_caught_by_the_image_count():
    v = check_fov_labels(_collapsed())
    assert not v.trusted
    assert v.n_images == 6 and v.n_labels == 1
    assert "source images" in v.reason


def test_collapse_is_still_caught_after_an_incremental_merge():
    """``merge_records_into_csv`` dedups on (well, fov, timepoint, nucleus_id),
    so a collapsed well loses its colliding rows and the duplicate-id signal
    with them. The image-count signal survives that."""
    df = pd.DataFrame({
        "fov": ["1", "1", "1"],
        "filename": SIX_IMAGES[:3],
        "nucleus_id": [1, 2, 3],
        "timepoint": ["8h"] * 3,
    })
    assert not check_fov_labels(df).trusted


def test_duplicate_nucleus_ids_are_caught_without_a_filename_column():
    df = pd.DataFrame({"fov": ["1"] * 4, "nucleus_id": [1, 2, 1, 2],
                       "timepoint": ["8h"] * 4})
    v = check_fov_labels(df)
    assert not v.trusted and "nucleus id" in v.reason


def test_more_images_across_timepoints_is_not_a_collapse():
    """Images accumulate with timepoints while field labels do not — comparing
    the two totals (rather than per timepoint) would fire on every healthy
    multi-timepoint well."""
    df = pd.DataFrame({
        "fov": ["1", "2", "1", "2"],
        "filename": ["a_1_0h.tif", "a_2_0h.tif", "a_1_8h.tif", "a_2_8h.tif"],
        "nucleus_id": [1, 1, 1, 1],
        "timepoint": ["0h", "0h", "8h", "8h"],
    })
    assert check_fov_labels(df).trusted


def test_empty_and_unknown_frames_are_trusted():
    assert check_fov_labels(None).trusted
    assert check_fov_labels(pd.DataFrame()).trusted
    # No fov column and no evidence of more than one image → nothing to report.
    assert check_fov_labels(pd.DataFrame({"nucleus_id": [1, 2]})).trusted


# ── The aggregation refuses to report per-field numbers it cannot compute ────


def _agg(df):
    pts = aggregate_with_threshold_df(df, threshold=250.0, use_sem=True,
                                      per_fov_spread=True)
    assert pts, "expected one aggregated timepoint"
    return pts[0]


def test_collapsed_well_reports_undefined_not_a_pooled_total():
    """The heart of it: the events-per-field value must not become the total."""
    _t, mean, spread, frac, n_above, _n_total, frac_spread, n_pf, n_pf_sd = _agg(
        _collapsed())
    assert math.isnan(spread) and math.isnan(frac_spread)
    assert math.isnan(n_pf) and math.isnan(n_pf_sd), (
        "the pooled total must not be reported as a mean per field"
    )
    # The mean and fraction do NOT depend on the field labels, so they stay.
    assert math.isfinite(mean) and math.isfinite(frac) and n_above > 0


def test_healthy_well_is_unaffected():
    _t, mean, spread, _frac, _n, _nt, frac_spread, n_pf, n_pf_sd = _agg(_healthy())
    assert math.isfinite(mean)
    assert spread > 0 and frac_spread > 0
    assert n_pf > 0 and n_pf_sd > 0


def test_single_field_well_keeps_its_count_but_has_no_spread():
    """One real field: the events-per-field mean is that field's count (valid),
    while the spread across fields is undefined (not zero)."""
    *_rest, n_pf, n_pf_sd = _agg(_single_field())
    assert n_pf == 20
    assert math.isnan(n_pf_sd)


# ── Undefined never renders or exports as zero ──────────────────────────────


def test_undefined_spread_is_nan_not_zero_in_per_cell_mode():
    """Per-cell mode with one cell above threshold: the SD is undefined."""
    df = pd.DataFrame({
        "fov": ["1", "1"], "timepoint_hours": [8.0, 8.0], "area_px": [200.0, 200.0],
        "Included": [1, 1], "gfp_mean_intensity": [900.0, 10.0],
    })
    pt = aggregate_with_threshold_df(df, threshold=250.0, use_sem=True)[0]
    assert pt[4] == 1                 # exactly one cell above threshold
    assert math.isnan(pt[2]), "one observation has no SD — that is not zero"


def test_export_writes_a_blank_cell_for_an_undefined_spread():
    from well_viewer.features.export.service import bar_metric_row

    row = bar_metric_row(
        mean=590.0, spread=float("nan"), frac=0.028, frac_spread=float("nan"),
        has=True, n_above=float("nan"), n_above_spread=float("nan"),
        ch="gfp", metric="mean_intensity", tp_str="8", threshold=250.0,
        band_lbl="SEM",
    )
    assert row["sem_gfp_mean_intensity"] == ""
    assert row["fraction_sem"] == ""
    assert row["events_total"] == ""
    assert row["events_per_replicate_sem"] == ""
    # A real spread still exports. ``replicate_unit`` is what the row's own
    # callers pass (both go through ``_replicate_unit_for``), and it is what
    # says this count is a per-FOV *mean* and so keeps its decimals. Omitting
    # it here described a row the app cannot produce — a per-FOV spread with no
    # unit naming it — and the float form was then being held up by the
    # whisker's sign rather than by the statistic.
    ok = bar_metric_row(
        mean=590.0, spread=31.1, frac=0.02, frac_spread=0.0033, has=True,
        n_above=87.0, n_above_spread=24.0, ch="gfp", metric="mean_intensity",
        tp_str="8", threshold=250.0, band_lbl="SEM", replicate_unit="fov",
    )
    assert ok["sem_gfp_mean_intensity"] == "31.100000"
    assert ok["events_total"] == "87.000000"
    assert ok["events_per_replicate_sem"] == "24.000000"

    # Without the toggle the same column is the integer count it says it is.
    counted = bar_metric_row(
        mean=590.0, spread=31.1, frac=0.02, frac_spread=0.0033, has=True,
        n_above=87.0, n_above_spread=24.0, ch="gfp", metric="mean_intensity",
        tp_str="8", threshold=250.0, band_lbl="SEM", replicate_unit="well",
    )
    assert counted["events_total"] == "87"


def test_fold_change_keeps_an_undefined_spread_undefined():
    """Normalising a bar must not turn "not computed" into a measured zero."""
    from well_viewer.domain.fold_change import scale_bar_value

    val, err = scale_bar_value(100.0, float("nan"), control_mean=50.0, op="ratio")
    assert val == pytest.approx(2.0)
    assert math.isnan(err)
    # A control spread of None still means "denominator treated as exact".
    val2, err2 = scale_bar_value(100.0, None, control_mean=50.0, op="ratio")
    assert val2 == pytest.approx(2.0) and err2 == 0.0


def test_bar_renderer_marks_a_bar_whose_spread_is_undefined():
    """No whisker is drawn either way — the marker is what distinguishes "not
    computed" from "error bars are switched off"."""
    pytest.importorskip("PySide6")
    from matplotlib.figure import Figure

    from well_viewer.features.barplot.collect import BarItem
    from well_viewer.features.barplot.controller import render_bar_items

    fig = Figure()
    ax_mean, ax_frac = fig.subplots(2)
    items = [
        BarItem(key="C02", display="C02", color="#888", mean=590.0,
                spread=float("nan"), has_mean=True, frac=0.028,
                frac_spread=float("nan"), has_frac=True, n_above=117.0,
                n_above_spread=float("nan"), tp=8.0, group_key=""),
        BarItem(key="D03", display="D03", color="#888", mean=647.0,
                spread=31.1, has_mean=True, frac=0.02, frac_spread=0.0033,
                has_frac=True, n_above=87.0, n_above_spread=24.0, tp=8.0,
                group_key=""),
    ]
    render_bar_items(
        ax_mean=ax_mean, ax_frac=ax_frac, use_groups=False, items=items,
        threshold=250.0, warn_color="#f00", border_color="#333",
        placeholder_color="#222", disabled_well_color="#444",
        err_bar_color="#ccc",
    )
    marks = [t for t in ax_mean.texts if t.get_text() == "n/a"]
    assert len(marks) == 1, "exactly the bar with no computable spread is marked"
    # The bar that does have a spread got a real error bar instead.
    assert ax_mean.containers, "the defined bar keeps its error bar"


def test_pipeline_warns_when_the_schema_leaves_the_fov_slot_empty(caplog):
    """The root cause, at the point it happens: a filename with fewer tokens
    than the schema silently writes an empty FOV for every row of that well."""
    import logging

    import process_microscopy as pm
    from util.schema import parse_schema

    schema = parse_schema("experiment:channel:well:fov:timepoint")
    pm._SHORT_FILENAME_WARNED.clear()
    with caplog.at_level(logging.WARNING, logger="process_microscopy"):
        assert pm.parse_filename("exp_gfp_C02_3_8h.tif", schema=schema)["fov"] == "3"
        assert pm.parse_filename("exp_gfp_C02.tif", schema=schema)["fov"] == ""
    msgs = [r.getMessage() for r in caplog.records]
    assert any("'fov' slot is empty" in m for m in msgs), msgs
    assert len(msgs) == 1, "the healthy filename must not warn"
