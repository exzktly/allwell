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
and the index-selection tests use points whose per-well and per-FOV pairs are
different numbers. With matching numbers every one of the three defects above
writes the right answer by accident (CLAUDE.md §7.8 #2) — and ``MockApp``'s
replicate statistics do produce matching numbers, which is why the pairs are
built by hand where the answer depends on which index was read.
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

# Per-well statistics of the set below. ``MockApp`` reduces its replicate
# statistics from the per-well points it was handed, and its per-FOV branch
# reduces the same field, so a collected AggPoint carries the SAME pair at
# indices 4/9 and 7/8 — the two answers this file is about are numerically
# identical through that fixture, and an assertion on the value alone would be
# satisfied by either index. The collector tests below therefore assert what
# only the collector can say (the unit, and that the FOV branch was taken), and
# the index selection itself is pinned against hand-built points whose two
# pairs differ. Measuring that was the point: the first draft of this file
# claimed a discriminating fixture it did not have.
DRAWN_EVENTS_WELL = 7.5      # mean of the per-well counts 7 and 8
DRAWN_EVENTS_WELL_SEM = 0.5  # their cross-well SEM
DRAWN_FRAC_SEM = 0.1         # SEM of the per-well fractions 0.4 and 0.6

#: A point whose per-well pair (indices 4 / 9) and per-FOV pair (7 / 8) are
#: different numbers, so a row reading the wrong one cannot print the right
#: answer by accident.
DISTINCT_PT = (0.0, 105.0, 5.0, 0.5, 7.5, 0, 0.1, 3.25, 0.75, 0.5, 15)


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

def test_the_row_reads_the_per_fov_pair_and_not_the_per_well_one():
    """The index selection, against a point whose two pairs differ.

    Indices 4 / 9 hold ``7.5 ± 0.5`` and 7 / 8 hold ``3.25 ± 0.75``. With
    Aggregate-FOVs on the Events panel draws the second pair, so the row must
    too; reading indices 4 / 9 unconditionally — the reported defect — prints
    the first.
    """
    row = line_metric_row(DISTINCT_PT, ch="gfp", metric="mean_intensity",
                          threshold=0.0, band_lbl="SEM", per_fov=True)
    assert row["n_above_replicate_mean"] == "3.250000"
    assert row["err_n_above_replicate_SEM"] == "0.750000"
    assert row["n_above_replicate_unit"] == "fov"

    off = line_metric_row(DISTINCT_PT, ch="gfp", metric="mean_intensity",
                          threshold=0.0, band_lbl="SEM", per_fov=False)
    assert off["n_above_replicate_mean"] == "7.500000"
    assert off["err_n_above_replicate_SEM"] == "0.500000"
    assert off["n_above_replicate_unit"] == "well"

    # Either way the integer column keeps meaning the cross-well total.
    assert row["n_above_threshold"] == off["n_above_threshold"] == 15


def test_a_replicate_set_in_fov_mode_reports_the_drawn_events():
    """Through the collector: the FOV branch is taken and the unit says so.

    Index 4 is a placeholder zero on that branch, so a row reading it would
    print ``0.000000`` — which is what the defect did, and what this fixture
    can still tell apart even though its two pairs coincide.
    """
    row = _row_for(_set_app(per_fov=True), per_fov=True)
    assert row["n_above_replicate_mean"] not in ("", "0.000000")
    assert row["err_n_above_replicate_SEM"] != ""
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
    assert rows[0]["n_above_replicate_mean"] not in ("", "0.000000")
    assert rows[0]["err_n_above_replicate_SEM"] != ""
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


# ── the integer count stays an integer, through the shape the app passes ─────

def test_the_event_count_keeps_its_integer_form_through_as_aggpoint():
    """``LinePoint.as_aggpoint`` floats index 4 so a replicate set's drawn mean
    is not truncated — and the exporter passes nothing else. Writing that value
    through unchanged turned a plain well's count from ``3`` into ``3.0``,
    which is what the column's own documentation calls an integer count and
    what a downstream ``int(row[...])`` chokes on.

    The two earlier tests of this column hand-build the tuple with a Python
    ``int`` at index 4, a shape the exporter never sees, so neither could
    notice (CLAUDE.md §7.8 face two).
    """
    from well_viewer.features.lineplot.controller import LinePoint

    nan = float("nan")
    point = LinePoint(t=0.0, mean=100.0, spread=1.0, frac=0.4, n_above=3,
                      n_total=20, frac_spread=nan, n_above_pf_mean=nan,
                      n_above_pf_spread=nan)
    assert point.as_aggpoint()[4] == 3.0        # the shape under test
    row = line_metric_row(point.as_aggpoint(), ch="gfp", metric="mean_intensity",
                          threshold=0.0, band_lbl="SEM")
    assert row["n_above_threshold"] == 3

    # A count that was never computed is blank, not the word "nan".
    missing = LinePoint(t=0.0, mean=nan, spread=0.0, frac=nan, n_above=nan,
                        n_total=20)
    assert line_metric_row(missing.as_aggpoint(), ch="gfp",
                           metric="mean_intensity", threshold=0.0,
                           band_lbl="SEM")["n_above_threshold"] == ""

    # Fold change scales this field into a ratio, which is genuinely
    # fractional and must not be rounded to an integer.
    scaled = LinePoint(t=0.0, mean=1.0, spread=0.0, frac=0.5, n_above=0.5,
                       n_total=20)
    assert line_metric_row(scaled.as_aggpoint(), ch="gfp",
                           metric="mean_intensity", threshold=0.0,
                           band_lbl="SEM")["n_above_threshold"] == "0.500000"


def test_a_replicate_row_leaves_the_cell_count_empty():
    """Index 5 is a placeholder on a replicate-set point — the set's statistic
    is a mean over its wells and never counted their cells — and a real
    per-well point always has at least one cell, because the aggregator emits
    no point for a timepoint with none. Printing the placeholder said the set
    held no cells at a timepoint where it plainly held some."""
    rset_pt = (0.0, 105.0, 5.0, 0.5, 7.5, 0, 0.1, 0.0, 0.0, 0.5, 15)
    assert line_metric_row(rset_pt, ch="gfp", metric="mean_intensity",
                           threshold=0.0, band_lbl="SEM")["n_total"] == ""

    well_pt = (0.0, 100.0, 1.0, 0.4, 7, 20, float("nan"), 0.0, 0.0)
    assert line_metric_row(well_pt, ch="gfp", metric="mean_intensity",
                           threshold=0.0, band_lbl="SEM")["n_total"] == 20


def test_a_blanked_fold_change_point_writes_no_spread_on_the_tab_export(tmp_path):
    """The on-tab twin of the batch guard: a member whose control does not
    resolve has its mean blanked by ``fold_change.blank_point``, which leaves
    the spread at 0.0 — a measured-looking zero for a division that did not
    happen. Fold change is switched on through the app's own state, and only
    the second collector pass (the fold-change one) is replaced, so the row is
    assembled by the real exporter."""
    from well_viewer.features.lineplot.controller import LinePoint, LineSeries

    app = MockApp(
        per_well_pts={
            "A01": [make_agg(0.0, 100.0, frac=0.4, n_above=7, n_total=20)],
        },
        fc_vs_control_on=True, fc_control_label="CTRL",
    )
    ctx = getattr(app, "_ctx", app)
    ctx.state.selection.selected_wells = {"A01"}
    raw, _band = collect_line_series(ctx, threshold=0.0, use_sem=True,
                                     fc_state=FC_OFF)
    blanked = [LineSeries(
        key="A01", display="A01", color="#000", kind="well", wells=["A01"],
        points=[LinePoint(t=0.0, mean=float("nan"), spread=0.0,
                          frac=float("nan"), has_mean=False, has_frac=False)],
        fc_outcome="control_unresolved")]

    passes = iter([(raw, "SEM"), (blanked, "SEM")])
    out = tmp_path / "fc.csv"
    with mock.patch.object(svc, "_collect_line_series",
                           lambda *_a, **_k: next(passes)), \
            mock.patch.object(svc, "ask_save_csv", return_value=str(out)):
        export_plot_data(ctx)
    row = list(csv.DictReader(io.StringIO(out.read_text())))[0]
    assert row["fold_change_mean"] == ""
    assert row["fold_change_sem"] == ""
    assert row["fold_change_outcome"] == "control_unresolved"


def test_a_control_the_plot_hides_is_not_in_the_file(tmp_path):
    """*Plotted controls: hidden* drops a member from the figure, and the file
    has to drop it too.

    The two passes do not agree about membership on their own: the raw pass
    runs with fold change **off**, and hiding is decided by the fold-change
    resolver, so the raw pass returns the hidden control like any other member.
    The file then carried rows for a curve nobody could see, with blank
    fold-change cells that read exactly like a normalization that failed.
    """
    from well_viewer.features.lineplot.controller import LinePoint, LineSeries

    app = MockApp(
        per_well_pts={
            "A01": [make_agg(0.0, 100.0, frac=0.4, n_above=7, n_total=20)],
            "A02": [make_agg(0.0, 110.0, frac=0.6, n_above=8, n_total=20)],
        },
        fc_vs_control_on=True, fc_control_label="A01",
    )
    ctx = getattr(app, "_ctx", app)
    ctx.state.selection.selected_wells = {"A01", "A02"}
    raw, _band = collect_line_series(ctx, threshold=0.0, use_sem=True,
                                     fc_state=FC_OFF)
    assert {s.key for s in raw} == {"A01", "A02"}
    # What the fold-change pass returns when A01 is the hidden control.
    drawn = [LineSeries(
        key="A02", display="A02", color="#000", kind="well", wells=["A02"],
        points=[LinePoint(t=0.0, mean=1.1, spread=0.05, frac=0.6)],
        fc_outcome="ok")]

    passes = iter([(raw, "SEM"), (drawn, "SEM")])
    out = tmp_path / "hidden.csv"
    with mock.patch.object(svc, "_collect_line_series",
                           lambda *_a, **_k: next(passes)), \
            mock.patch.object(svc, "ask_save_csv", return_value=str(out)):
        export_plot_data(ctx)
    rows = list(csv.DictReader(io.StringIO(out.read_text())))
    assert [r["well"] for r in rows] == ["A02"]


# ── the fold-change half of the exporter, which nothing drove ───────────────

def _fc_app():
    """Two wells, one of them the control, fold change vs control on."""
    app = MockApp(
        per_well_pts={
            "A01": [make_agg(0.0, 100.0, frac=0.4, n_above=7, n_total=20)],
            "A02": [make_agg(0.0, 200.0, frac=0.6, n_above=8, n_total=20)],
        },
        fc_vs_control_on=True, fc_control_label="A01",
    )
    ctx = getattr(app, "_ctx", app)
    ctx.state.selection.selected_wells = {"A01", "A02"}
    return ctx


def test_the_raw_columns_stay_raw_while_the_fold_change_columns_normalise(tmp_path):
    """The additive schema, driven end to end.

    Two passes exist so the file carries both: the raw pass is collected with
    ``FC_STATE_OFF``. Dropping that argument — so the "raw" columns quietly
    carry normalized values — is invisible to any test that does not export
    with fold change **active**, and nothing did. A01 is the control, so its
    ratio is 1; A02 is twice it.
    """
    rows = _exported(_fc_app(), tmp_path, "fc")
    by_well = {r["well"]: r for r in rows}
    assert set(by_well) == {"A01", "A02"}
    # Raw means, untouched by the normalization beside them.
    assert by_well["A01"]["mean_gfp_mean_intensity"] == "100.000000"
    assert by_well["A02"]["mean_gfp_mean_intensity"] == "200.000000"
    # The normalized value, in its own column.
    assert by_well["A01"]["fold_change_mean"] == "1.000000"
    assert by_well["A02"]["fold_change_mean"] == "2.000000"


def test_every_fold_change_row_says_what_it_was_divided_by_and_why(tmp_path):
    """``fold_change_control`` and ``fold_change_outcome`` are the only things
    separating a normalization that failed from one that was never asked for,
    and both were unasserted anywhere."""
    rows = _exported(_fc_app(), tmp_path, "fc_ctrl")
    by_well = {r["well"]: r for r in rows}
    assert all(r["fold_change_mode"] == "control" for r in rows)
    assert by_well["A02"]["fold_change_control"] == "A01"
    assert by_well["A02"]["fold_change_outcome"] in ("ok", "self")
    # The control divided by itself is still a row, and says so.
    assert by_well["A01"]["fold_change_control"] == "A01"


def test_no_fold_change_columns_appear_when_it_is_off(tmp_path):
    """The schema is additive: with the feature off the columns are absent
    rather than present and empty, which is what every reader keys on."""
    rows = _exported(_solo_app(per_fov=False), tmp_path, "no_fc")
    assert "fold_change_mean" not in rows[0]
    assert "fold_change_outcome" not in rows[0]
