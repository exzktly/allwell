"""The Line tab's CSV states the numbers its panels draw.

Three quantities the Line Graphs tab plots were missing or wrong in the CSV it
exports, and each failure was invisible in the file itself — a blank cell and a
``0.000000`` cell both read as data:

* the **fraction spread**. The Fraction panel bands its curve with the SD/SEM
  at AggPoint index 6, and the line CSV had no column for it at all, on the
  per-tab export and the batch one alike. The bar CSV has carried
  ``err_frac_<band>`` all along, so the same figure exported from the two tabs
  disagreed about whether the number exists.
* the **Events replicate columns** under Aggregate-FOVs. ``line_metric_row``
  read the per-well count and its cross-well spread (indices 4 / 9)
  unconditionally, but in FOV mode those are a placeholder zero and a zero,
  and the panel draws the per-FOV mean and spread (indices 7 / 8). A replicate
  set exported ``0.000000`` events with a ``0.000000`` spread beside a curve
  sitting at 7.5, and a lone well exported its integer count where the panel
  drew a per-field mean.
* the **on-tab export's aggregation**. It re-aggregated each selected well
  with a bare ``aggregate_well`` instead of asking the tab's collector, so the
  points were plain 9-tuples: Aggregate-FOVs was ignored, no fraction spread
  was ever computed, and ``err_n_above_replicate_*`` / ``n_above_replicate_unit``
  could not be anything but empty on any plate — which is how the bug was
  reported.

Fixtures give the two wells **different** fractions and **different** counts,
and give the per-FOV fields values that differ from the per-well ones. With
matching numbers every one of the three defects above writes the right answer
by accident (CLAUDE.md §7.8 #2).
"""
from __future__ import annotations

import csv
import io
from unittest import mock


from well_viewer.features.export import service as svc
from well_viewer.features.export.service import (
    export_plot_data, line_metric_fieldnames, line_metric_row,
)
from well_viewer.features.lineplot.controller import collect_line_series

from tests.conftest import MockApp, make_agg

FC_OFF = (False, "", False)

# Per-well statistics of the set below, and the per-FOV ones beside them. Every
# pair differs, so a row reading the wrong index cannot print the right number.
DRAWN_EVENTS_WELL = 7.5      # mean of the per-well counts 7 and 8
DRAWN_EVENTS_WELL_SEM = 0.5  # their cross-well SEM
DRAWN_EVENTS_FOV = 7.5       # mean of the per-FOV counts, pooled across wells
DRAWN_EVENTS_FOV_SEM = 0.5
DRAWN_FRAC_SEM = 0.1         # SEM of the per-well fractions 0.4 and 0.6


def _set_app(*, per_fov: bool):
    """One replicate set of two wells that disagree on every quantity."""
    app = MockApp(
        per_well_pts={
            "A01": [make_agg(0.0, 100.0, frac=0.4, n_above=7, n_total=20,
                             frac_spread=0.05, n_above_pf_mean=3.5,
                             n_above_pf_spread=0.5)],
            "A02": [make_agg(0.0, 110.0, frac=0.6, n_above=8, n_total=20,
                             frac_spread=0.07, n_above_pf_mean=4.0,
                             n_above_pf_spread=0.7)],
        },
        selections=[{"name": "S", "wells": ["A01", "A02"], "hidden": False}],
    )
    ctx = getattr(app, "_ctx", app)
    ctx.state.plot.use_fov_replicates = per_fov
    return ctx


def _solo_app(*, per_fov: bool):
    """One selected well, aggregated the way the app aggregates it.

    With Aggregate-FOVs off ``aggregate_with_threshold_df`` leaves every
    per-field statistic **NaN** — there is one sample of the fraction and
    nothing to vary across — so the fixture carries NaN rather than
    ``make_agg``'s convenience zero. A zero there is a spread that was
    measured, and using it would have let a row that fabricates one pass.
    """
    nan = float("nan")
    pt = (make_agg(0.0, 100.0, frac=0.4, n_above=7, n_total=20,
                   frac_spread=0.05, n_above_pf_mean=3.5, n_above_pf_spread=0.5)
          if per_fov else
          make_agg(0.0, 100.0, frac=0.4, n_above=7, n_total=20,
                   frac_spread=nan, n_above_pf_mean=nan, n_above_pf_spread=nan))
    app = MockApp(per_well_pts={"A01": [pt]})
    ctx = getattr(app, "_ctx", app)
    ctx.state.selection.selected_wells = {"A01"}
    ctx.state.plot.use_fov_replicates = per_fov
    return ctx


def _row_for(ctx, *, per_fov: bool, band_lbl: str = "SEM") -> dict:
    """The CSV row for the first drawn point of the first drawn series."""
    series, collected_band = collect_line_series(
        ctx, threshold=0.0, use_sem=True, fc_state=FC_OFF)
    assert series, "the collector drew nothing — the fixture is not plotting"
    assert collected_band == band_lbl
    return line_metric_row(
        series[0].points[0].as_aggpoint(), ch="gfp", metric="mean_intensity",
        threshold=0.0, band_lbl=band_lbl, per_fov=per_fov)


def _exported(ctx, tmp_path, name="line") -> list[dict]:
    """Run the real on-tab exporter and parse the file it wrote."""
    out = tmp_path / f"{name}.csv"
    with mock.patch.object(svc, "ask_save_csv", return_value=str(out)):
        export_plot_data(ctx)
    assert out.exists(), "the exporter wrote no file"
    text = out.read_text()
    assert text.strip(), "the exporter wrote an empty file"
    return list(csv.DictReader(io.StringIO(text)))


# ── the fraction spread ──────────────────────────────────────────────────────

def test_the_line_csv_has_a_fraction_spread_column():
    cols = line_metric_fieldnames("gfp", "mean_intensity", "SEM")
    assert "err_frac_SEM" in cols
    # Beside the value it is the spread of, the way the bar CSV orders them.
    assert cols.index("err_frac_SEM") == cols.index("fraction_above") + 1


def test_the_fraction_spread_the_panel_bands_with_reaches_the_csv():
    """The Fraction panel draws 0.5 ± 0.1 here; the CSV said only 0.5."""
    row = _row_for(_set_app(per_fov=False), per_fov=False)
    assert row["fraction_above"] == "0.500000"
    assert row["err_frac_SEM"] == f"{DRAWN_FRAC_SEM:.6f}"


def test_an_uncomputed_fraction_spread_is_an_empty_cell():
    """A lone well with Aggregate-FOVs off has no second sample of the
    fraction, so nothing was computed — and 0.000000 would claim otherwise."""
    row = _row_for(_solo_app(per_fov=False), per_fov=False)
    assert row["fraction_above"] == "0.400000"
    assert row["err_frac_SEM"] == ""


# ── the Events columns under Aggregate-FOVs ─────────────────────────────────

def test_a_replicate_set_in_fov_mode_reports_the_drawn_events():
    """Index 4 is a placeholder zero on this branch; the panel draws index 7."""
    row = _row_for(_set_app(per_fov=True), per_fov=True)
    assert row["n_above_replicate_mean"] == f"{DRAWN_EVENTS_FOV:.6f}"
    assert row["err_n_above_replicate_SEM"] == f"{DRAWN_EVENTS_FOV_SEM:.6f}"
    assert row["n_above_replicate_unit"] == "fov"
    # The integer column keeps meaning the cross-well total.
    assert row["n_above_threshold"] == 15


def test_a_replicate_set_without_fov_mode_reports_the_per_well_mean():
    row = _row_for(_set_app(per_fov=False), per_fov=False)
    assert row["n_above_replicate_mean"] == f"{DRAWN_EVENTS_WELL:.6f}"
    assert row["err_n_above_replicate_SEM"] == f"{DRAWN_EVENTS_WELL_SEM:.6f}"
    assert row["n_above_replicate_unit"] == "well"


def test_a_solo_well_in_fov_mode_reports_its_per_field_mean():
    """Its fields are its replicates: the panel draws 3.5 ± 0.5, not the 7
    events the well holds in total."""
    row = _row_for(_solo_app(per_fov=True), per_fov=True)
    assert row["n_above_replicate_mean"] == "3.500000"
    assert row["err_n_above_replicate_SEM"] == "0.500000"
    assert row["n_above_replicate_unit"] == "fov"
    assert row["n_above_threshold"] == 7


def test_a_solo_well_without_fov_mode_is_unchanged():
    """The drawn value IS the well's count, and there is nothing to average
    over — so the spread and unit cells stay empty."""
    row = line_metric_row(
        (0.0, 100.0, 0.0, 0.5, 9, 20, float("nan"), 0.0, 0.0),
        ch="gfp", metric="mean_intensity", threshold=0.0, band_lbl="SEM")
    assert row["n_above_threshold"] == 9
    assert row["n_above_replicate_mean"] == "9.000000"
    assert row["err_n_above_replicate_SEM"] == ""
    assert row["n_above_replicate_unit"] == ""
    assert row["err_frac_SEM"] == ""


def test_a_replicate_spread_of_zero_is_written():
    """Two wells that agree exactly is a measurement, not an absence — the
    same rule the bar CSV's column follows."""
    row = line_metric_row(
        (0.0, 100.0, 1.0, 0.5, 5.0, 20, 0.0, 0.0, 0.0, 0.0, 10),
        ch="gfp", metric="mean_intensity", threshold=0.0, band_lbl="SEM")
    assert row["err_n_above_replicate_SEM"] == "0.000000"
    assert row["err_frac_SEM"] == "0.000000"
    assert row["n_above_replicate_unit"] == "well"


# ── the on-tab exporter, driven for real ─────────────────────────────────────

def test_the_on_tab_export_writes_one_row_per_drawn_set(tmp_path):
    """Replicate sets active → the file describes the curves, with the
    replicate columns filled in. Per-well rows could not fill them at all."""
    rows = _exported(_set_app(per_fov=False), tmp_path, "sets")
    assert len(rows) == 1
    row = rows[0]
    assert row["replicate_set"] == "S"
    assert row["wells"] == "A01;A02"
    assert row["n_wells"] == "2"
    assert row["mean_gfp_mean_intensity"] == "105.000000"
    assert row["n_above_replicate_mean"] == f"{DRAWN_EVENTS_WELL:.6f}"
    assert row["err_n_above_replicate_SEM"] == f"{DRAWN_EVENTS_WELL_SEM:.6f}"
    assert row["n_above_replicate_unit"] == "well"
    assert row["err_frac_SEM"] == f"{DRAWN_FRAC_SEM:.6f}"


def test_the_on_tab_export_honours_aggregate_fovs(tmp_path):
    """The toggle used to reach neither the aggregation nor the columns."""
    rows = _exported(_set_app(per_fov=True), tmp_path, "sets_fov")
    assert len(rows) == 1
    assert rows[0]["n_above_replicate_mean"] == f"{DRAWN_EVENTS_FOV:.6f}"
    assert rows[0]["err_n_above_replicate_SEM"] == f"{DRAWN_EVENTS_FOV_SEM:.6f}"
    assert rows[0]["n_above_replicate_unit"] == "fov"


def test_the_on_tab_export_of_selected_wells_keeps_its_well_columns(tmp_path):
    """Per-well mode is unchanged apart from the new spread column."""
    rows = _exported(_solo_app(per_fov=False), tmp_path, "wells")
    assert len(rows) == 1
    assert rows[0]["well"] == "A01"
    assert "replicate_set" not in rows[0]
    assert rows[0]["mean_gfp_mean_intensity"] == "100.000000"
    assert rows[0]["fraction_above"] == "0.400000"
    assert rows[0]["err_frac_SEM"] == ""


def test_the_on_tab_export_refuses_an_empty_selection(tmp_path):
    """Nothing drawn → a warning and no file, rather than a header-only CSV
    that reads as a plate with no data."""
    app = MockApp(per_well_pts={"A01": [make_agg(0.0, 100.0)]})
    ctx = getattr(app, "_ctx", app)
    ctx.state.selection.selected_wells = set()
    ctx.state.selection.selections = []
    ctx.window = None          # the warning names a parent widget
    out = tmp_path / "empty.csv"
    with mock.patch.object(svc, "ask_save_csv", return_value=str(out)), \
            mock.patch.object(svc, "_warn") as warned:
        export_plot_data(ctx)
    assert warned.called
    assert not out.exists()
