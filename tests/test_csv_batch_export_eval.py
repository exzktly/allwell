"""End-to-end evaluation of every CSV export and batch export path.

This module is a *vigorous, behavioural* audit of All-Well's export surface.
It drives a real, fully-loaded ``WellViewerApp`` on the offscreen Qt platform
(no mocking of the aggregation / collection layers) and asserts the actual
bytes written to disk for:

  On-tab "Export CSV" buttons  (``features/export/service.py``)
    * line  — ``export_plot_data``      (single wells + replicate-set fallback)
    * bar   — ``export_bar_plot_data``  (well mode + replicate-set/group mode)
    * scatter cells   — ``export_scatter_data``
    * heatmap         — ``export_heatmap_data``
    * distribution    — ``export_distribution_data``

  Batch Export tab panels      (``features/batch_export/``)
    * line  batch — ``base_panel._run_batch``
    * bar   batch — ``bar_panel._run_batch``   (pooled + expanded replicates)
    * scatter-cells batch — ``scatter_panel._run_batch``
    * atomic staging + manifest (``service.commit_staged_export``)

The unifying theme required by the task is **well groups defined as
replicate sets and as single wells**: every batch group here mixes a
``ReplicateSet`` (multi-well replicate) with a ``solo_well`` (single well),
and the on-tab exports are exercised with both a raw single-well selection
and an active replicate-set selection.

All expected numbers are the closed-form values of the deterministic
``tests/fixtures/mini_dataset`` (see ``cell_gfp`` etc.), so the assertions
pin exact aggregates, not just structure.

Wells / values used throughout (channel ``gfp``, metric ``mean_intensity``,
6 cells/timepoint, timepoints 0/24/48 h):

    GFP cell value = 100 + well_index*10 + tp_h*2 + cell   (cell = 1..6)
    well_index: A01=0, A02=1, B01=2, B02=3

    per-well mean(tp)      = base + tp_h*2 + 3.5
    A01: 103.5 / 151.5 / 199.5     A02: 113.5 / 161.5 / 209.5
    B01: 123.5 / 171.5 / 219.5     B02: 133.5 / 181.5 / 229.5
    pooled mean of A01+A02(tp0) = 108.5   (pool of 12 cells)
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import pytest

from tests.fixtures.mini_dataset import build_mini_dataset

# The whole module builds the full Qt app → belongs in the slow lane, exactly
# like the ``viewer_app``-based integration tests (conftest auto-marks those;
# this module uses its own module-scoped app, so mark it by hand).
pytestmark = pytest.mark.slow


# ── module-scoped fully-loaded app ───────────────────────────────────────────
#
# Constructing ``WellViewerApp`` under the offscreen QPA platform is expensive
# (~1 min), so build it ONCE for the module and reset the cheap per-test state
# (selection / replicate sets) between tests.

@pytest.fixture(scope="module")
def eval_app(tmp_path_factory):
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    app = QApplication.instance() or QApplication([])
    # Never let a real modal dialog block a headless run.
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
    QMessageBox.warning = staticmethod(lambda *a, **k: 0)
    QMessageBox.critical = staticmethod(lambda *a, **k: 0)
    QMessageBox.information = staticmethod(lambda *a, **k: 0)
    QMessageBox.question = staticmethod(lambda *a, **k: 0)

    data_dir = build_mini_dataset(tmp_path_factory.mktemp("mini") / "ds")
    from well_viewer.runtime_app import WellViewerApp

    va = WellViewerApp()
    va._load_path(data_dir)
    yield va
    va.deleteLater()


@pytest.fixture
def ctx(eval_app):
    """Fresh-ish per-test view: reset selection state before each test."""
    c = eval_app._ctx
    c.state.selection.selections = []
    c.state.selection.selected_wells = set()
    return c


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_csv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def _run_ontab_export(ctx, fn, tmp_path, name="out") -> list[dict] | None:
    """Run an on-tab export with the save dialog stubbed to *tmp_path*.

    Returns the parsed rows, or ``None`` when the exporter warned/aborted
    (no file written).
    """
    from well_viewer.features.export import service as svc

    out = Path(tmp_path) / f"{name}.csv"
    with mock.patch.object(svc, "_ask_save_csv", return_value=str(out)):
        fn(ctx)
    if not out.exists():
        return None
    return _parse_csv(out.read_text())


def _select_single_wells(ctx, wells):
    ctx.state.selection.selections = []
    ctx.state.selection.selected_wells = set(wells)


def _define_replicate_sets(ctx, sets):
    """sets: list of (name, [wells]). Clears the manual selection so the
    exporters take their replicate-set path."""
    ctx.state.selection.selections = [
        {"name": n, "wells": list(w), "hidden": False} for n, w in sets
    ]
    ctx.state.selection.selected_wells = set()


def _make_mixed_group(name="Cond1"):
    """A batch group mixing a replicate set (A01+A02) with a single well (B01)
    — the exact 'replicates and single wells' construct the task calls for."""
    from well_viewer.domain.batch_models import BarGroup, ReplicateSet

    return BarGroup(
        name,
        members=[ReplicateSet("RepA", ["A01", "A02"])],
        solo_wells=["B01"],
    )


# =============================================================================
# 1. ON-TAB LINE EXPORT  (export_plot_data)
# =============================================================================

def test_ontab_line_single_wells(ctx, tmp_path):
    """Single-well selection → one block of rows per well, no replicate_set
    column, exact per-well means across all three timepoints."""
    from well_viewer.features.export.service import export_plot_data

    _select_single_wells(ctx, ["A01", "B01"])
    rows = _run_ontab_export(ctx, export_plot_data, tmp_path, "line_wells")
    assert rows is not None

    assert "replicate_set" not in rows[0]  # no grouping column for raw wells
    a01 = [r for r in rows if r["well"] == "A01"]
    b01 = [r for r in rows if r["well"] == "B01"]
    assert len(a01) == 3 and len(b01) == 3  # 3 timepoints each

    means_a01 = {r["time_h"]: r["mean_gfp_mean_intensity"] for r in a01}
    assert means_a01 == {
        "0.0000": "103.500000",
        "24.0000": "151.500000",
        "48.0000": "199.500000",
    }
    # column schema
    assert set(a01[0]) == {
        "well", "well_name", "time_h", "mean_gfp_mean_intensity",
        "sem_gfp_mean_intensity", "n_above_threshold", "fraction_above",
        "n_total", "threshold", "metric",
    }


def test_ontab_line_replicate_set_fallback(ctx, tmp_path):
    """No manual selection but replicate sets defined → export falls back to
    the rep-set members and adds a ``replicate_set`` column tagging each row."""
    from well_viewer.features.export.service import export_plot_data

    _define_replicate_sets(ctx, [("CTRL", ["A01", "A02"])])
    rows = _run_ontab_export(ctx, export_plot_data, tmp_path, "line_rep")
    assert rows is not None

    assert "replicate_set" in rows[0]
    assert {r["well"] for r in rows} == {"A01", "A02"}
    assert all(r["replicate_set"] == "CTRL" for r in rows)
    # values are still the raw per-well means (rep-set only tags grouping)
    a02_tp48 = next(r for r in rows if r["well"] == "A02" and r["time_h"] == "48.0000")
    assert a02_tp48["mean_gfp_mean_intensity"] == "209.500000"


def test_batch_line_solo_well_uses_sample_label(ctx):
    """The Batch Export line collector must resolve a solo well's legend label
    through the same sample-label map the live Line Graphs tab uses
    (``well_display_label``), not the bare well token. Regression for the
    well-number-in-legend bug: a labelled well shows its sample name, an
    unlabelled one falls back to the token."""
    from well_viewer.features.batch_export._line_series_runner import (
        collect_line_series_for_group,
    )
    from well_viewer.domain.batch_models import BarGroup

    val_col = f"{ctx.state.channel.active_channel}_{ctx.state.channel.active_metric}"
    prev_labels = dict(ctx.state.dataset.well_labels)
    ctx.state.dataset.well_labels = {"B01": "Treated"}
    try:
        series = collect_line_series_for_group(
            ctx, BarGroup("G", members=[], solo_wells=["B01", "B02"]),
            threshold=-1.0, use_sem=True, val_col=val_col, fluor_gates={},
        )
    finally:
        ctx.state.dataset.well_labels = prev_labels

    by_key = {s.key: s for s in series}
    assert by_key["B01"].display == "Treated"   # labelled → sample name
    assert by_key["B02"].display == "B02"        # unlabelled → bare token


def test_batch_bar_group_uses_sample_labels(ctx):
    """The Batch Export bar collector must resolve both a solo well's and a
    replicate set's display label the same way the live Bar Plot tab does
    (``well_display_label`` / ``replicate_display_label``), not emit the
    bare well token / rep-set name. Regression: ``collect_bar_items_for_group``
    used to hand back ``(rset.name, rset.name, ...)`` and ``(w, w, ...)``
    straight through to ``BarItem.display``, so a custom Home → Well Labels
    label never reached the batch-exported bar figure even though the
    on-tab collector (``collect_bar_items``) already resolved it."""
    from well_viewer.features.barplot.controller import (
        FC_STATE_OFF, collect_bar_items_for_group,
    )
    from well_viewer.domain.batch_models import BarGroup, ReplicateSet

    val_col = f"{ctx.state.channel.active_channel}_{ctx.state.channel.active_metric}"
    prev_labels = dict(ctx.state.dataset.well_labels)
    ctx.state.dataset.well_labels = {"B01": "Treated"}
    try:
        grp = BarGroup("Cond1", members=[ReplicateSet("RepA", ["A01", "A02"])],
                       solo_wells=["B01"])
        items = collect_bar_items_for_group(
            ctx, grp, 0.0,
            val_col=val_col, threshold=-1.0, use_sem=True,
            per_fov_spread=False, fc_state=FC_STATE_OFF,
        )
    finally:
        ctx.state.dataset.well_labels = prev_labels

    by_key = {it.key: it for it in items}
    assert by_key["B01"].display == "Treated"   # labelled solo well → sample name
    assert by_key["RepA"].display == "RepA"     # rep-set with no custom label → its own name


# =============================================================================
# 2. ON-TAB BAR EXPORT  (export_bar_plot_data)
# =============================================================================

def test_ontab_bar_well_mode(ctx, tmp_path):
    """Single wells, no rep-sets → ``well`` id column, per-well mean at the
    active timepoint (t0)."""
    from well_viewer.features.export.service import export_bar_plot_data

    _select_single_wells(ctx, ["A01", "B01"])
    rows = _run_ontab_export(ctx, export_bar_plot_data, tmp_path, "bar_wells")
    assert rows is not None
    assert "well" in rows[0] and "name" not in rows[0]

    by_well = {r["well"]: r for r in rows}
    assert by_well["A01"]["mean_gfp_mean_intensity"] == "103.500000"
    assert by_well["B01"]["mean_gfp_mean_intensity"] == "123.500000"
    assert by_well["A01"]["timepoint_h"] == "0"


def test_ontab_bar_group_mode(ctx, tmp_path):
    """Active replicate sets → ``name`` id column keyed by rep-set name, with
    the mean-of-per-well-means aggregate (CTRL = mean(103.5, 113.5) = 108.5)."""
    from well_viewer.features.export.service import export_bar_plot_data

    _define_replicate_sets(ctx, [("CTRL", ["A01", "A02"]), ("TREAT", ["B01", "B02"])])
    rows = _run_ontab_export(ctx, export_bar_plot_data, tmp_path, "bar_groups")
    assert rows is not None
    assert "name" in rows[0] and "well" not in rows[0]

    by_name = {r["name"]: r for r in rows}
    assert set(by_name) == {"CTRL", "TREAT"}
    assert by_name["CTRL"]["mean_gfp_mean_intensity"] == "108.500000"
    assert by_name["TREAT"]["mean_gfp_mean_intensity"] == "128.500000"
    # SEM of the two per-well means: sd([103.5,113.5])/sqrt(2) = 5.0
    assert by_name["CTRL"]["err_SEM_gfp_mean_intensity"] == "5.000000"


# =============================================================================
# 3. ON-TAB SCATTER / HEATMAP / DISTRIBUTION EXPORT
# =============================================================================

def test_ontab_scatter_cells(ctx, tmp_path):
    """Per-cell scatter export → one row per cell of each selected well."""
    from well_viewer.features.export.service import export_scatter_data

    _select_single_wells(ctx, ["A01", "B01"])
    rows = _run_ontab_export(ctx, export_scatter_data, tmp_path, "scatter")
    assert rows is not None
    # 2 wells * 6 cells * 3 timepoints = 36 datapoints
    assert len(rows) == 36
    assert {"group_well", "well_name", "timepoint_h"} <= set(rows[0])
    assert {r["group_well"] for r in rows} == {"A01", "B01"}


def test_ontab_heatmap(ctx, tmp_path):
    """Heatmap export → one row per plate cell (8x12 = 96), value column named
    for the active channel/metric."""
    from well_viewer.features.export.service import export_heatmap_data

    # The heatmap must be drawn before it can be exported; the loaded app drew
    # it during load. Export the current grid.
    rows = _run_ontab_export(ctx, export_heatmap_data, tmp_path, "heatmap")
    assert rows is not None
    assert len(rows) == 96
    assert {"row", "col", "wells", "well_names", "timepoint_h"} <= set(rows[0])


def test_ontab_distribution(ctx, tmp_path):
    """Per-cell distribution export → one row per cell across selected wells."""
    from well_viewer.features.export.service import export_distribution_data

    _select_single_wells(ctx, ["A01", "B01"])
    rows = _run_ontab_export(ctx, export_distribution_data, tmp_path, "dist")
    assert rows is not None
    assert len(rows) > 0
    assert {"group", "well_name", "timepoint_h"} <= set(rows[0])


# =============================================================================
# 4. BATCH LINE EXPORT  (base_panel._run_batch)
# =============================================================================

def _run_line_batch(ctx, groups, out_dir):
    from well_viewer.features.batch_export import BatchExportPanel

    panel = BatchExportPanel(ctx, None, use_sidebar_groups=False)
    panel._groups = list(groups)
    panel._out_dir_edit.setText(str(out_dir))
    panel._run_batch()
    return panel


def test_batch_line_replicates_and_single_wells(ctx, tmp_path):
    """Batch line export of a group mixing a replicate set and a single well.

    Asserts the ``member_type`` column distinguishes ``replicate_set`` from
    ``solo_well``, the pooled vs solo means match the dataset, and the run
    commits atomically (CSV + figure + manifest)."""
    out_dir = tmp_path / "batch_line"
    panel = _run_line_batch(ctx, [_make_mixed_group("Cond1")], out_dir)
    assert "✓" in panel._prog_lbl.text(), panel._prog_lbl.text()

    csv_path = out_dir / "batch_Cond1.csv"
    assert csv_path.exists()
    rows = _parse_csv(csv_path.read_text())

    rep = [r for r in rows if r["member"] == "RepA"]
    solo = [r for r in rows if r["member"] == "B01"]
    assert rep and solo
    assert all(r["member_type"] == "replicate_set" for r in rep)
    assert all(r["member_type"] == "solo_well" for r in solo)
    assert rep[0]["wells"] == "A01;A02" and rep[0]["n_wells"] == "2"
    assert solo[0]["wells"] == "B01" and solo[0]["n_wells"] == "1"

    rep_by_t = {r["time_h"]: r["mean_gfp_mean_intensity"] for r in rep}
    # Mean-of-per-well-means (the tab's rep-set statistic): mean(A01, A02).
    # Equals pool-of-cells here only because the fixture has equal cell counts.
    assert rep_by_t["0.0000"] == "108.500000"      # mean(103.5, 113.5)
    assert rep_by_t["48.0000"] == "204.500000"     # mean(199.5, 209.5)
    solo_by_t = {r["time_h"]: r["mean_gfp_mean_intensity"] for r in solo}
    assert solo_by_t["0.0000"] == "123.500000"     # B01 solo

    # atomic commit: a figure and a manifest listing exactly what was written
    figs = list(out_dir.glob("batch_Cond1.*"))
    assert any(f.suffix != ".csv" for f in figs)   # the rendered figure
    manifest = json.loads((out_dir / "batch_export_manifest.json").read_text())
    assert "batch_Cond1.csv" in manifest["files"]
    assert manifest["count"] == len(manifest["files"])


def test_batch_line_regression_figure_render(ctx, tmp_path):
    """Regression: the line batch figure render collects a CDF via
    ``_line_series_runner._cdf_for``, which imported a non-existent name
    (``all_fluor_values``) — every line batch export failed and committed
    NOTHING. Guard that a plain line batch now produces its CSV + figure."""
    out_dir = tmp_path / "batch_line_reg"
    from well_viewer.domain.batch_models import BarGroup, ReplicateSet

    grp = BarGroup("G", members=[ReplicateSet("R", ["A01", "A02"])], solo_wells=[])
    panel = _run_line_batch(ctx, [grp], out_dir)
    assert "error" not in panel._prog_lbl.text().lower(), panel._prog_lbl.text()
    assert (out_dir / "batch_G.csv").exists()
    # a non-CSV figure file was committed alongside the CSV
    assert any(p.suffix != ".csv" and p.name.startswith("batch_G")
               for p in out_dir.iterdir())


def test_batch_line_figure_has_four_panels_incl_n_above(ctx):
    """Regression for the reported bug: the on-tab Line Graphs figure has FOUR
    stacked panels (mean / fraction / N(above) / CDF) but the batch line figure
    dropped the N(above) panel. The batch figure now reuses the tab's renderer,
    so it must have four axes with the N(above) panel third."""
    from well_viewer.features.batch_export import BatchExportPanel

    panel = BatchExportPanel(ctx, None, use_sidebar_groups=False)
    grp = _make_mixed_group("Cond1")
    with panel._app_group_scope(grp), panel._app_val_col_scope("gfp_mean_intensity"):
        fig = panel._render_group_figure(grp, 50.0, True, "SEM")
    assert len(fig.axes) == 4, [ax.get_ylabel() for ax in fig.axes]
    # Panel order matches the tab: mean / fraction / N(above) / CDF.
    assert fig.axes[2].get_ylabel().startswith("N(above)")
    assert "CDF" in fig.axes[3].get_title() or "Cumulative" in fig.axes[3].get_ylabel()


def test_batch_line_solo_well_error_matches_within_well(ctx, tmp_path):
    """A solo well in a batch group keeps its within-well cell spread (the tab's
    per-well statistic), NOT a replicate spread of 0 — i.e. a solo is never
    silently treated as a one-well replicate set."""
    out_dir = tmp_path / "batch_line_solo"
    from well_viewer.domain.batch_models import BarGroup

    _run_line_batch(ctx, [BarGroup("G", members=[], solo_wells=["B01"])], out_dir)
    rows = _parse_csv((out_dir / "batch_G.csv").read_text())
    solo = [r for r in rows if r["member"] == "B01"]
    assert solo and all(r["member_type"] == "solo_well" for r in solo)
    # Within-well SEM across B01's 6 cells is > 0 (a 1-well replicate spread
    # would be exactly 0).
    assert any(float(r["sem_gfp_mean_intensity"]) > 0 for r in solo)


# =============================================================================
# 5. BATCH BAR EXPORT  (bar_panel._run_batch) — pooled and expanded
# =============================================================================

def _run_bar_batch(ctx, groups, out_dir, *, pool_replicates=True, tps=("0",)):
    from well_viewer.features.batch_export import BarBatchExportPanel

    panel = BarBatchExportPanel(ctx, None, use_sidebar_groups=False)
    panel._groups = list(groups)
    panel._out_dir_edit.setText(str(out_dir))
    panel._pool_reps_cb.setChecked(pool_replicates)
    # Restrict to the requested timepoint(s) for tight assertions.
    panel._tp_lb.clearSelection()
    for i in range(panel._tp_lb.count()):
        it = panel._tp_lb.item(i)
        if it.text() in tps:
            it.setSelected(True)
    panel._run_batch()
    return panel


def test_batch_bar_pooled_replicates_and_single_wells(ctx, tmp_path):
    """Pooled bar batch → replicate set collapses to ONE ``replicate_set`` row,
    single well is a ``solo_well`` row."""
    out_dir = tmp_path / "batch_bar_pool"
    panel = _run_bar_batch(ctx, [_make_mixed_group("Cond1")], out_dir,
                           pool_replicates=True, tps=("0",))
    assert "✓" in panel._prog_lbl.text(), panel._prog_lbl.text()

    csv_path = out_dir / "bar_Cond1_t0.csv"
    assert csv_path.exists()
    rows = _parse_csv(csv_path.read_text())
    by_member = {r["member"]: r for r in rows}

    assert by_member["RepA"]["member_type"] == "replicate_set"
    assert by_member["RepA"]["n_wells"] == "2"
    assert by_member["RepA"]["mean_gfp_mean_intensity"] == "108.500000"
    assert by_member["B01"]["member_type"] == "solo_well"
    assert by_member["B01"]["mean_gfp_mean_intensity"] == "123.500000"


def test_batch_bar_expanded_replicates_and_single_wells(ctx, tmp_path):
    """Expanded bar batch (pool off) → each replicate-set member becomes its own
    ``replicate_set_member:<name>`` row; the single well stays ``solo_well``."""
    out_dir = tmp_path / "batch_bar_expand"
    panel = _run_bar_batch(ctx, [_make_mixed_group("Cond1")], out_dir,
                           pool_replicates=False, tps=("0",))
    assert "✓" in panel._prog_lbl.text(), panel._prog_lbl.text()

    rows = _parse_csv((out_dir / "bar_Cond1_t0.csv").read_text())
    by_member = {r["member"]: r for r in rows}

    assert by_member["A01"]["member_type"] == "replicate_set_member:RepA"
    assert by_member["A02"]["member_type"] == "replicate_set_member:RepA"
    assert by_member["B01"]["member_type"] == "solo_well"
    # expanded members carry their own raw per-well means
    assert by_member["A01"]["mean_gfp_mean_intensity"] == "103.500000"
    assert by_member["A02"]["mean_gfp_mean_intensity"] == "113.500000"


# =============================================================================
# 6. CROSS-PATH CONSISTENCY  (on-tab == batch, byte-for-byte metric columns)
# =============================================================================

def test_cross_path_solo_well_bytes_match(ctx, tmp_path):
    """A single well's bar metric columns are byte-identical whether written by
    the on-tab exporter or the batch exporter — they share the same
    ``bar_metric_row`` builder, and this proves it end to end."""
    from well_viewer.features.export.service import export_bar_plot_data
    from well_viewer.domain.batch_models import BarGroup

    # on-tab, well mode, A01 at t0
    _select_single_wells(ctx, ["A01"])
    ontab = _run_ontab_export(ctx, export_bar_plot_data, tmp_path, "xp_ontab")
    ontab_a01 = next(r for r in ontab if r["well"] == "A01")

    # batch, solo well A01 at t0
    out_dir = tmp_path / "xp_batch"
    _run_bar_batch(ctx, [BarGroup("G", members=[], solo_wells=["A01"])], out_dir,
                   pool_replicates=True, tps=("0",))
    batch_a01 = next(r for r in _parse_csv((out_dir / "bar_G_t0.csv").read_text())
                     if r["member"] == "A01")

    metric_cols = [
        "timepoint_h", "mean_gfp_mean_intensity", "err_SEM_gfp_mean_intensity",
        "fraction_above", "err_frac_SEM", "n_above_threshold",
        "err_n_above_SEM", "threshold", "metric",
    ]
    for col in metric_cols:
        assert ontab_a01[col] == batch_a01[col], (
            f"{col}: on-tab={ontab_a01[col]!r} batch={batch_a01[col]!r}")


# =============================================================================
# 7. BATCH SCATTER-CELLS EXPORT  (scatter_panel._run_batch)
# =============================================================================

def _run_scatter_cells_batch(ctx, groups, out_dir, *, tps=("0.0",)):
    from well_viewer.features.batch_export import ScatterBatchExportPanel

    panel = ScatterBatchExportPanel(ctx, None, scatter_mode="cells",
                                    use_sidebar_groups=False)
    panel._groups = list(groups)
    panel._out_dir_edit.setText(str(out_dir))
    panel._tp_lb.clearSelection()
    for i in range(panel._tp_lb.count()):
        it = panel._tp_lb.item(i)
        if it.text() in tps:
            it.setSelected(True)
    panel._run_batch()
    return panel


def test_batch_scatter_cells_replicates_and_single_wells(ctx, tmp_path):
    """Scatter-cells batch of a mixed group writes per-cell rows covering every
    well of the replicate set AND the single well."""
    out_dir = tmp_path / "batch_scatter"
    panel = _run_scatter_cells_batch(ctx, [_make_mixed_group("Cond1")], out_dir,
                                     tps=("0.0",))
    assert "error" not in panel._prog_lbl.text().lower(), panel._prog_lbl.text()

    csvs = list(out_dir.glob("scatter_cells_Cond1*.csv"))
    assert csvs, list(out_dir.iterdir())
    rows = _parse_csv(csvs[0].read_text())
    wells = {r["well"] for r in rows}
    # replicate members A01, A02 and the solo well B01 all represented
    assert {"A01", "A02", "B01"} <= wells
    assert {"group", "member", "well", "timepoint_h", "x", "y"} <= set(rows[0])


def _run_scatter_agg_batch(ctx, groups, out_dir, *, tps=("0.0",)):
    from well_viewer.features.batch_export import ScatterBatchExportPanel

    panel = ScatterBatchExportPanel(ctx, None, scatter_mode="aggregate",
                                    use_sidebar_groups=False)
    panel._groups = list(groups)
    panel._out_dir_edit.setText(str(out_dir))
    panel._tp_lb.clearSelection()
    for i in range(panel._tp_lb.count()):
        it = panel._tp_lb.item(i)
        if it.text() in tps:
            it.setSelected(True)
    panel._run_batch()
    return panel


def test_batch_scatter_agg_replicates_and_single_wells(ctx, tmp_path):
    """Aggregate scatter batch of a mixed group emits ONE aggregated point per
    replicate set and one per single well, with the single well's spread = 0
    (no replicates) and the replicate set's spread = per-well-mean SEM."""
    out_dir = tmp_path / "batch_scatter_agg"
    panel = _run_scatter_agg_batch(ctx, [_make_mixed_group("Cond1")], out_dir,
                                   tps=("0.0",))
    assert "error" not in panel._prog_lbl.text().lower(), panel._prog_lbl.text()

    csvs = list(out_dir.glob("scatter_aggregate_Cond1*.csv"))
    assert csvs, list(out_dir.iterdir())
    rows = _parse_csv(csvs[0].read_text())
    by_label = {r["label"].split(" ")[0]: r for r in rows}
    assert "RepA" in by_label and "B01" in by_label   # replicate set + solo well
    assert by_label["RepA"]["x"] == "108.5"           # pooled/per-well-mean GFP
    assert by_label["RepA"]["x_err"] == "5"           # SEM of per-well means
    assert by_label["B01"]["x"] == "123.5"            # solo well GFP
    assert by_label["B01"]["x_err"] == "0"            # single well → no spread


def test_batch_scatter_cells_honors_display_options(ctx, tmp_path, monkeypatch):
    """The scatter-cells batch figure reuses the tab's render_scatter_points and
    honours the new panel options: density shading, per-axis log, and the
    Overlay-vs-Separate-axes layout (one panel per group when separated)."""
    from well_viewer.features.batch_export import ScatterBatchExportPanel
    from well_viewer.domain.batch_models import BarGroup, ReplicateSet
    from well_viewer.features.scatter.controller import collect_scatter_data

    panel = ScatterBatchExportPanel(ctx, None, scatter_mode="cells",
                                    use_sidebar_groups=False)
    panel._sc_density_cb.setChecked(True)
    panel._sc_xlog_cb.setChecked(True)
    panel._sc_ylog_cb.setChecked(False)
    panel._sc_layout_cb.setCurrentText("Separate axes")

    captured: dict = {}
    monkeypatch.setattr(panel, "_save_figure",
                        lambda fig, p, f: captured.update(fig=fig))

    grp = BarGroup("Cond1", members=[ReplicateSet("RepA", ["A01", "A02"])],
                   solo_wells=["B01"])
    col_x = panel._scatter_axis_col(panel._sc_cells_x_cb, panel._sc_cells_metric_x_cb)
    col_y = panel._scatter_axis_col(panel._sc_cells_y_cb, panel._sc_cells_metric_y_cb)
    with panel._app_group_scope(grp):
        data = collect_scatter_data(ctx, col_x, col_y, [0.0],
                                    well_colors=["#888", "#aaa", "#ccc"])
        panel._render_scatter_cells_figure(
            data, col_x=col_x, col_y=col_y, tps_list=[0.0],
            title="t", fig_path=tmp_path / "x.png", fmt="png")

    fig = captured["fig"]
    # Separate axes → one panel per group (RepA + B01).
    assert len(fig.axes) == len(data) >= 2
    ax0 = fig.axes[0]
    assert getattr(ax0, "_aw_force_xlog", False) is True    # X log applied
    assert getattr(ax0, "_aw_force_ylog", True) is False    # Y linear
    # Density shading draws a per-point PathCollection tagged for density.
    assert any(getattr(c, "_aw_density_collection", False) for c in ax0.collections)
    # State is restored — the batch never leaves the live Scatter tab dirty.
    assert ctx.state.scatter.gates_hidden is False
    assert ctx.state.scatter.color_by_tp is False


def test_batch_scatter_agg_honors_log_scale(ctx, tmp_path, monkeypatch):
    """The aggregate scatter batch applies the X/Y log toggles to the figure."""
    from well_viewer.features.batch_export import ScatterBatchExportPanel
    from well_viewer.domain.batch_models import BarGroup, ReplicateSet

    panel = ScatterBatchExportPanel(ctx, None, scatter_mode="aggregate",
                                    use_sidebar_groups=False)
    panel._sc_agg_xlog_cb.setChecked(True)
    panel._sc_agg_ylog_cb.setChecked(False)
    captured: dict = {}
    monkeypatch.setattr(panel, "_save_figure",
                        lambda fig, p, f: captured.update(fig=fig))

    grp = BarGroup("Cond1", members=[ReplicateSet("RepA", ["A01", "A02"])],
                   solo_wells=["B01"])
    err = panel._run_scatter_agg_job(grp, 0.0, tmp_path / "a.csv",
                                     tmp_path / "a.png", "png")
    assert err is None, err
    ax0 = captured["fig"].axes[0]
    assert getattr(ax0, "_aw_force_xlog", False) is True
    assert not getattr(ax0, "_aw_force_ylog", False)   # Y stays linear


# =============================================================================
# 8. ATOMIC-STAGING GUARANTEE AT THE PANEL LEVEL
# =============================================================================

def test_batch_failure_preserves_prior_export(ctx, tmp_path, monkeypatch):
    """If a group's figure render raises mid-run, the staged output is
    discarded and any previous complete export is left untouched — no partial
    CSV leaks into the output directory."""
    out_dir = tmp_path / "batch_atomic"
    # First, a clean successful export establishes a prior artefact.
    _run_line_batch(ctx, [_make_mixed_group("Cond1")], out_dir)
    prior = (out_dir / "batch_Cond1.csv").read_text()
    assert prior

    # Now force the figure render to blow up and re-run.
    from well_viewer.features.batch_export import base_panel
    monkeypatch.setattr(
        base_panel.BatchExportPanel, "_render_group_figure",
        lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    panel = _run_line_batch(ctx, [_make_mixed_group("Cond1")], out_dir)
    assert "error" in panel._prog_lbl.text().lower()

    # Prior export intact, no staging dir left behind.
    assert (out_dir / "batch_Cond1.csv").read_text() == prior
    assert not any(p.name.startswith(".batch_export_tmp_") for p in out_dir.iterdir())
