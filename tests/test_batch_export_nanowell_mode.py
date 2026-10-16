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
    expected = wells_summary(site_metrics(data, "earliest"), ["A01"], "n", channels=data.channels)
    n_rows = [r for r in rows if r["metric"] == "n"]
    assert [(r["well"], r["timepoint"], int(r["n_sites"]), int(r["n_used"]), float(r["median"])) for r in n_rows] == [
        (w, t, s, u, m) for w, t, s, u, m in expected[["well", "timepoint", "n_sites", "n_used", "median"]]
        .itertuples(index=False)]
    assert {r["metric"] for r in rows if r["well"] == "A01"} == {"n", "c"}
    assert [r["note"] for r in rows if r["well"] == "B07"] == ["no nanowell table"]


def test_the_array_map_writes_one_figure_per_well(panel, tmp_path, monkeypatch):
    p, _ctx = panel
    calls = _drawn(monkeypatch)
    p._view_cb.setCurrentIndex(p._view_cb.findData("array_map"))
    p._run_batch()
    assert sorted(f.name for f in (tmp_path / "export").glob("*.png")) == [
        "nanowell_Drug_A_c_A01.png", "nanowell_Drug_A_n_A01.png"]
    assert [(c["view"], c["well"]) for c in calls] == [("array_map", "A01"), ("array_map", "A01")]


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
    p._figure_view = {"view": "array_map", "metric": "c", "axis": "time", "baseline": "earliest",
                      "cohort": True, "well": "A01", "timepoint": "0h"}
    seen = {}

    def capture(ctx_, tab):
        seen.update(view=st.view, metric=st.metric, cohort=st.cohort, well=st.well, tab=tab)
        return {}, {}

    import review.features.batch_export.base_panel as base

    original = base._capture_params
    base._capture_params = capture
    try:
        p._capture_group_params(p._groups[0], None)
    finally:
        base._capture_params = original
    assert seen == {"view": "array_map", "metric": "c", "cohort": True, "well": "A01", "tab": "Nanowells"}
    assert (st.view, st.metric, st.cohort) == ("distribution", "n", False)


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
    p._view_cb.setCurrentIndex(p._view_cb.findData("array_map"))
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

    def boom(_path):
        raise NanowellSchemeError("scheme_version 9 is not one this version reads")

    shown = []
    monkeypatch.setattr(nanowell_panel, "_load_nanowell_data", boom)
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, text: shown.append((title, text)))
    assert p._load("earliest") is None
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
    monkeypatch.setattr(nanowell_panel, "_load_nanowell_data", lambda _path: pytest.fail("read the tables again"))
    data, _metrics = p._load("earliest")
    assert data is st.data
    controller.set_baseline(st, "first_measurable")
    assert st.loaded_for == (str(ctx.state.dataset.data_dir), "first_measurable")
