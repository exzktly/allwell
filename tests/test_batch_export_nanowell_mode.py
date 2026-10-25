"""Batch Export's Nanowells mode (tracker I4): per export group, the chosen view per metric and a summary CSV.

Driven through the real panel constructor and the real run, on a real nanowell run's output. What is pinned is the
half that is this mode's: the figures are drawn by the page's own ``draw_view`` with the group's wells and the
panel's choices (asserted at that call, the boundary), one file per metric — per well on the Array map — the CSV
carries the page's own summary numbers and names a group's well that has no nanowell table, and a gallery record
restores to the figure's view rather than to the page's.
"""

from __future__ import annotations

import csv

import pytest

pytest.importorskip("PySide6")

from review.domain.batch_models import BarGroup  # noqa: E402
from tests.test_nanowell_review_load import run_with_a_cell  # noqa: E402,F401  (module fixture)


@pytest.fixture
def panel(qtbot, run_with_a_cell, tmp_path):  # noqa: F811
    from PySide6.QtCore import Qt

    from review.features.batch_export.nanowell_panel import NanowellBatchExportPanel
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, run_with_a_cell)
    p = NanowellBatchExportPanel(ctx, window)
    p._groups = [BarGroup("Drug A", solo_wells=["A01", "B07"])]   # B07 has no nanowell table
    p._out_dir_edit.setText(str(tmp_path / "export"))
    for i in range(p._metric_list.count()):
        item = p._metric_list.item(i)
        item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in ("n", "c") else Qt.Unchecked)
    p._threshold_edits["n"].setText("2")             # every ticked metric needs its cut (tracker I9)
    p._threshold_edits["c"].setText("0.15")
    return p, ctx


def _drawn(monkeypatch):
    """Spy on the page's composition, still drawing through it."""
    from review.features.batch_export import nanowell_panel
    from review.features.nanowell import controller

    calls = []
    real = controller.draw_view

    def spy(fig, view, *args, **kwargs):
        calls.append({"view": view, **{k: kwargs[k] for k in ("metric", "groups", "well", "tableless", "cohort")}})
        return real(fig, view, *args, **kwargs)

    monkeypatch.setattr(nanowell_panel, "_draw_view", spy)     # the panel's own handle on it
    return calls


def test_each_metric_is_the_page_s_figure_of_the_group_and_the_csv_holds_the_page_s_summary(panel, tmp_path,
                                                                                             monkeypatch):
    from review.features.nanowell.collect import load_nanowell_data
    from review.features.nanowell.distribution_compute import wells_summary
    from review.features.nanowell.series_compute import site_metrics

    p, ctx = panel
    calls = _drawn(monkeypatch)
    p._run_batch()
    out = tmp_path / "export"
    assert sorted(f.name for f in out.glob("nanowell_*")) == [
        "nanowell_Drug_A_c.png", "nanowell_Drug_A_n.png", "nanowell_Drug_A_summary.csv"]
    assert [(c["view"], c["metric"], c["groups"], c["tableless"]) for c in calls] == [
        ("distribution", "n", {"A01": ["A01"]}, ["B07"]), ("distribution", "c", {"A01": ["A01"]}, ["B07"])]
    rows = list(csv.DictReader((out / "nanowell_Drug_A_summary.csv").open()))
    data = load_nanowell_data(ctx.state.dataset.data_dir)
    expected = wells_summary(site_metrics(data, "earliest"), ["A01"], "n", channels=data.channels,
                             responding_above=2.0)
    n_rows = [r for r in rows if r["metric"] == "n"]
    assert [(r["well"], r["timepoint"], int(r["n_sites"]), int(r["n_used"]), float(r["median"]),
             float(r["fraction_responding"]), r["responding_above"]) for r in n_rows] == [
        (w, t, s, u, m, f, "2.0") for w, t, s, u, m, f in
        expected[["well", "timepoint", "n_sites", "n_used", "median", "fraction_responding"]].itertuples(index=False)]
    assert {r["metric"] for r in rows if r["well"] == "A01"} == {"n", "c"}
    assert [r["note"] for r in rows if r["well"] == "B07"] == ["no nanowell table"]


def test_the_array_map_writes_one_figure_per_well(panel, tmp_path, monkeypatch):
    p, _ctx = panel
    calls = _drawn(monkeypatch)
    p._view_cb.setCurrentIndex(p._view_cb.findData("array"))
    p._run_batch()
    assert sorted(f.name for f in (tmp_path / "export").glob("*.png")) == [
        "nanowell_Drug_A_c_A01.png", "nanowell_Drug_A_n_A01.png"]
    assert [(c["view"], c["well"]) for c in calls] == [("array", "A01"), ("array", "A01")]
    # Drawn as the lattice map, and its gallery record restores those layers, not whatever the page was left on.
    from review.features.nanowell.state import ArrayLayers

    assert p._figure_view.get("layers") == ArrayLayers.preset("map")


def test_a_group_with_no_nanowell_table_is_skipped_and_says_so(panel, tmp_path):
    p, _ctx = panel
    p._groups = [BarGroup("Empty", solo_wells=["B07"]), BarGroup("Drug A", solo_wells=["A01"])]
    p._run_batch()
    assert sorted(f.name for f in (tmp_path / "export").glob("nanowell_*.png")) == [
        "nanowell_Drug_A_c.png", "nanowell_Drug_A_n.png"]


def test_a_gallery_record_restores_the_figure_s_view_and_the_page_is_not_moved(panel):
    p, ctx = panel
    st = ctx.state.feature_slice("nanowell")
    st.view, st.metric = "distribution", "n"
    from review.features.nanowell.state import ArrayLayers

    page_layers = st.layers
    p._figure_view = {"view": "array", "metric": "c", "axis": "time", "baseline": "earliest",
                      "cohort": True, "well": "A01", "timepoint": "0h", "layers": ArrayLayers.preset("map")}
    seen = {}

    def capture(ctx_, tab):
        seen.update(view=st.view, metric=st.metric, cohort=st.cohort, well=st.well, tab=tab,
                    positions=st.layers.positions)
        return {}, {}

    import review.features.batch_export.base_panel as base

    original = base._capture_params
    base._capture_params = capture
    try:
        p._capture_group_params(p._groups[0], None)
    finally:
        base._capture_params = original
    assert seen == {"view": "array", "metric": "c", "cohort": True, "well": "A01", "tab": "Nanowells",
                    "positions": "lattice"}
    assert (st.view, st.metric, st.cohort, st.layers) == ("distribution", "n", False, page_layers)


def test_a_group_s_replicate_sets_stay_sets_and_its_solo_wells_stand_alone():
    """The grouping the page draws: a set keeps its wells together, a solo well is its own group, and a well with
    no nanowell table is left out of both (it is named on the figure and in the CSV instead)."""
    from review.domain.batch_models import ReplicateSet
    from review.features.batch_export.nanowell_panel import NanowellBatchExportPanel

    grp = BarGroup("G", members=[ReplicateSet("Drug", ["A01", "A02", "H12"])], solo_wells=["B03", "A02", "C09"])
    groups = NanowellBatchExportPanel._figure_groups(None, grp, {"A01", "A02", "B03"})
    assert groups == {"Drug": ["A01", "A02"], "B03": ["B03"]}


def test_an_array_map_at_a_timepoint_the_well_lacks_says_so(panel, tmp_path, monkeypatch):
    """A batch run asked for one timepoint; a well not imaged then gets a figure saying that, not the map's
    "no addressed nanowells" placeholder (a different claim) and not another timepoint's map."""
    from review.features.batch_export import nanowell_panel

    p, _ctx = panel
    calls = _drawn(monkeypatch)
    messages = []
    monkeypatch.setattr(nanowell_panel, "_render_message", lambda fig, text, gid: messages.append(text))
    p._view_cb.setCurrentIndex(p._view_cb.findData("array"))
    p._tp_cb.addItem("48h", "48h")
    p._tp_cb.setCurrentIndex(p._tp_cb.findData("48h"))
    p._run_batch()
    assert calls == []
    assert messages == ["A01 has no timepoint 48h: it was not imaged then, so there is no map to draw."] * 2


def test_unreadable_nanowell_tables_are_refused_with_the_reason(panel, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from review.features.batch_export import nanowell_panel
    from review.features.nanowell.collect import NanowellSchemeError

    p, ctx = panel
    ctx.state.feature_slice("nanowell").data = None

    def boom(_path, **_kw):
        raise NanowellSchemeError("scheme_version 9 is not one this version reads")

    shown = []
    monkeypatch.setattr(nanowell_panel, "_load_nanowell_data", boom)
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, text: shown.append((title, text)))
    assert p._load("earliest", ["A01"]) is None
    assert shown and "scheme_version 9" in shown[0][1]


def test_tables_loaded_by_load_into_are_reused_by_the_batch_run(panel, monkeypatch):
    """Roll-up review X16: ``load_into`` left ``loaded_for`` as ``None``, and the batch run indexed it. It records
    what it loaded, as the page's own load does, and the batch run reuses those tables instead of reading again."""
    from review.features.batch_export import nanowell_panel
    from review.features.nanowell import controller

    p, ctx = panel
    st = ctx.state.feature_slice("nanowell")
    controller.load_into(st, ctx.state.dataset.data_dir)
    assert st.loaded_for == (str(ctx.state.dataset.data_dir), "earliest")
    monkeypatch.setattr(nanowell_panel, "_load_nanowell_data", lambda _path, **_kw: pytest.fail("read the tables again"))
    data, _metrics = p._load("earliest", st.data.wells)
    assert data is st.data
    controller.set_baseline(st, "first_measurable")
    assert st.loaded_for == (str(ctx.state.dataset.data_dir), "first_measurable")


def test_a_batch_run_reads_the_wells_it_exports_when_the_page_has_not(panel, monkeypatch):
    """The Nanowells page reads only the wells it draws, so its tables need not hold a batch run's wells: the run
    reads exactly its own then, and uses the page's tables only when they hold every one of them."""
    from review.features.batch_export import nanowell_panel
    from review.features.nanowell.collect import load_nanowell_data

    p, ctx = panel
    st = ctx.state.feature_slice("nanowell")
    data_dir = ctx.state.dataset.data_dir
    st.data, st.loaded_for = load_nanowell_data(data_dir, wells=[]), (str(data_dir), "earliest")
    asked = []
    monkeypatch.setattr(nanowell_panel, "_load_nanowell_data",
                        lambda path, wells=None: asked.append(wells) or load_nanowell_data(path, wells=wells))
    data, _metrics = p._load("earliest", ["A01", "B07"])
    assert asked == [["A01", "B07"]] and data.wells == ["A01"]       # B07 has no table, so it is not read
    st.data = data
    assert p._load("earliest", ["A01", "B07"])[0] is data and len(asked) == 1     # now held: not read again


def test_a_batch_whose_wells_have_no_nanowell_table_says_so_rather_than_raising(panel, monkeypatch):
    """Only the batch's wells are read, so when none of them has a table the loaded data holds no site and the
    metrics were computed over nothing: ``KeyError: 'state'``."""
    from PySide6.QtWidgets import QMessageBox

    p, ctx = panel
    ctx.state.feature_slice("nanowell").data = None
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, text: shown.append((title, text)))
    assert p._load("earliest", ["B07"]) is None                      # B07 has no nanowell table
    assert shown and "None of the groups' wells has a nanowell table" in shown[0][1]


def test_the_page_s_subset_is_used_and_the_csv_is_of_the_nanowells_kept(panel, tmp_path, monkeypatch):
    """The run's one cell sits in nanowell (1, 2) at 0h, so "≥1 cell at baseline" keeps that nanowell alone: the
    figure is drawn with the page's subset and every CSV row counts one nanowell, naming the subset."""
    from review.features.nanowell.subset_compute import Subset

    p, ctx = panel
    st = ctx.state.feature_slice("nanowell")
    st.subset = Subset.starting(1, None)
    p.window().show()                                           # the page's subset is named on show
    p.show()
    assert p._subset_label.text() == "Starting cells ≥1"
    calls = []
    from review.features.batch_export import nanowell_panel
    from review.features.nanowell import controller

    real = controller.draw_view
    monkeypatch.setattr(nanowell_panel, "_draw_view",
                        lambda fig, view, *a, **k: calls.append(k["subset"]) or real(fig, view, *a, **k))
    p._run_batch()
    assert calls == [Subset.starting(1, None)] * 2
    rows = [r for r in csv.DictReader((tmp_path / "export" / "nanowell_Drug_A_summary.csv").open())
            if r["well"] == "A01"]
    assert rows and {int(r["n_sites"]) for r in rows} == {1}
    assert {r["subset"] for r in rows} == {"Starting cells ≥1"}
    assert all(r["note"].startswith("Starting cells ≥1 (count at the earliest timepoint): 1 of ") for r in rows)
