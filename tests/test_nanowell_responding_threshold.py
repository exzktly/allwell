"""Fraction responding is counted above a cut the user sets, one per metric, with no default (tracker I9, U12).

The fixed cuts (1 for a fold change, 0 for a log₂ fold change, none for a measurement) are gone: a metric with
no cut set has no fraction responding, and the figure says so. These hold the arithmetic on hand-built frames,
then drive the page's real field and Batch Export's real panel.

The page fixture's latest timepoint is ``t_a``, where A01's nanowells hold 2, 4, 3 and 1 cells and B01's hold
3, (occluded), 1 and 2. The cut used is 2, so "above" and "at or above" give different answers in both wells
(A01 50% against 75%, B01 33% against 67%).
"""

from __future__ import annotations

import csv
import math

import pandas as pd
import pytest

from review.features.nanowell.summary_compute import group_summary, well_summary

pytest.importorskip("PySide6")


def _metrics(values: dict) -> pd.DataFrame:
    """A metrics frame: ``well -> [value per nanowell]`` at one timepoint, the value in both ``n`` and ``v``."""
    rows = [{"well": w, "timepoint": "t", "time_h": 0.0, "tp_order": 0, "n": v, "v": v}
            for w, vals in values.items() for v in vals]
    return pd.DataFrame(rows)


def _summary(values: dict, cut=None) -> pd.DataFrame:
    frame = _metrics(values)
    return well_summary(frame, "v", reasons=pd.Series("", index=frame.index), responding_above=cut)


def test_without_a_cut_no_fraction_is_computed():
    """A fold change used to count against 1 whatever the user wanted; now nothing is assumed."""
    summary = _summary({"A": [0.5, 1.5, 3.0]})
    assert math.isnan(summary.loc[0, "fraction_responding"])
    assert math.isnan(group_summary(summary, {"g": ["A"]}).loc[0, "fraction_responding"])


def test_the_fraction_is_of_the_nanowells_strictly_above_the_cut():
    summary = _summary({"A": [1.0, 2.0, 2.0, 3.0]}, 2.0)
    assert summary.loc[0, "fraction_responding"] == 0.25


def test_a_group_s_fraction_is_the_mean_of_its_wells_not_of_its_nanowells():
    """Wells are the replicate unit: A has 3 of 4 above, B 0 of 2, so the group is 37.5%, not the pooled 50%."""
    summary = _summary({"A": [5.0, 5.0, 5.0, 0.0], "B": [0.0, 0.0]}, 1.0)
    assert group_summary(summary, {"g": ["A", "B"]}).loc[0, "fraction_responding"] == 0.375


def test_the_saved_cuts_round_trip_and_anything_else_is_refused():
    from review.features.nanowell.state import NanowellState

    st = NanowellState()
    st.apply_config({"thresholds": {"n": 2, "brightness_fc_GFP": 1.5, "bad": "x", "nan": float("nan"),
                                    "flag": True, 3: 1.0}})
    assert st.thresholds == {"n": 2.0, "brightness_fc_GFP": 1.5}
    again = NanowellState()
    again.apply_config(st.to_config())
    assert again.thresholds == st.thresholds
    again.apply_config({"thresholds": "n=2"})
    assert again.thresholds == {}


# ── the page, through its real field ─────────────────────────────────────────
@pytest.fixture
def page(qtbot, tmp_path):
    from tests.test_nanowell_page import _page, _write_nanowell_dataset

    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    ctx.state.selection.selected_wells = {"A01", "B01"}
    return ctx


def _footer(ctx) -> list[str]:
    from tests.test_nanowell_page import _texts

    return _texts(ctx, "nw-footer")


def _type(ctx, text: str) -> None:
    edit = ctx.widgets.get("_nw_responding_edit")
    edit.setText(text)
    edit.editingFinished.emit()


def _cut_lines(ctx) -> list[float]:
    fig = ctx.widgets.get("_nw_fig")
    return [line.get_ydata()[0] for ax in fig.axes for line in ax.lines if line.get_gid() == "nw-responding"]


def test_a_cut_typed_on_the_page_is_stated_with_each_well_s_fraction_and_drawn(page, qtbot):
    from tests.test_nanowell_page import _loaded

    st = _loaded(qtbot, page)
    edit = page.widgets.get("_nw_responding_edit")
    assert edit.isVisible() and edit.text() == ""
    assert "Fraction responding is not computed: no threshold is set for Cell count (Responding above)." \
        in _footer(page)
    assert _cut_lines(page) == []

    _type(page, "2")
    assert st.thresholds == {"n": 2.0}
    assert ("Responding: above 2. Fraction responding (mean over wells) at t_a: A01 50%, B01 33%."
            in _footer(page)), _footer(page)
    assert _cut_lines(page) == [2.0, 2.0]                     # one line on each well's panel


def test_the_cut_is_kept_per_metric_and_bad_text_changes_nothing(page, qtbot):
    from tests.test_nanowell_page import _loaded

    st = _loaded(qtbot, page)
    edit = page.widgets.get("_nw_responding_edit")
    _type(page, "2")
    combo = page.widgets.get("_nw_metric_cb")
    combo.setCurrentIndex(combo.findData("confluence_fc"))
    assert edit.text() == "" and _cut_lines(page) == []      # a cut for Cell count is not a cut for this
    _type(page, "1.25")
    combo.setCurrentIndex(combo.findData("n"))
    assert edit.text() == "2" and st.thresholds == {"n": 2.0, "confluence_fc": 1.25}

    _type(page, "two")
    assert edit.text() == "2" and st.thresholds["n"] == 2.0  # put back, not cleared
    _type(page, "")
    assert "n" not in st.thresholds and _cut_lines(page) == []


def test_along_wells_the_fraction_is_stated_at_the_timepoint_drawn(page, qtbot):
    from tests.test_nanowell_page import _loaded

    _loaded(qtbot, page)
    page.widgets.get("_nw_axis_seg").setCurrentIndex(1)
    _type(page, "2")
    assert ("Responding: above 2. Fraction responding (mean over wells) at t_a: A01 50%, B01 33%."
            in _footer(page)), _footer(page)


# ── Batch Export: one cut per ticked metric, and none may be missing ─────────
@pytest.fixture
def panel(qtbot, tmp_path):
    from PySide6.QtCore import Qt

    from review.features.batch_export._common import BarGroup
    from review.features.batch_export.nanowell_panel import NanowellBatchExportPanel
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.test_nanowell_page import _write_nanowell_dataset

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, _write_nanowell_dataset(tmp_path / "data"))
    ctx.state.channel.active_channel = "gfp"
    p = NanowellBatchExportPanel(ctx, window)
    p._groups = [BarGroup("G", solo_wells=["A01", "B01"])]
    p._out_dir_edit.setText(str(tmp_path / "export"))

    def tick(*keys):
        for i in range(p._metric_list.count()):
            item = p._metric_list.item(i)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in keys else Qt.Unchecked)

    return p, ctx, tick, tmp_path / "export"


def test_a_run_with_a_ticked_metric_missing_its_cut_is_refused_and_names_it(panel, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    p, _ctx, tick, out = panel
    tick("n", "c")
    p._threshold_edits["n"].setText("2")
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, text: shown.append((title, text)))
    p._run_batch()
    assert [t for t, _ in shown] == ["Responding above not set"] and shown[0][1].endswith("\nConfluence")
    assert not out.exists() or not list(out.rglob("*.csv"))


def test_the_page_s_cut_seeds_the_field_and_a_typed_one_survives_unticking(panel):
    from PySide6.QtGui import QShowEvent

    p, ctx, tick, _out = panel
    assert p._threshold_edits["n"].text() == ""                # Cell count is ticked from the start
    ctx.state.feature_slice("nanowell").thresholds = {"n": 3.0}  # set on the page after the panel was built
    p.showEvent(QShowEvent())
    assert p._threshold_edits["n"].text() == "3"
    p._threshold_edits["n"].setText("4")
    tick("c")
    tick("n")
    assert p._threshold_edits["n"].text() == "4"


def test_the_csv_counts_at_each_metric_s_own_cut(panel):
    p, _ctx, tick, out = panel
    tick("n", "c")
    p._threshold_edits["n"].setText("2")
    p._threshold_edits["c"].setText("0.25")
    p._run_batch()
    rows = list(csv.DictReader(next(out.rglob("*_summary.csv")).open()))
    at = {(r["metric"], r["well"], r["timepoint"]): (r["responding_above"], r["fraction_responding"]) for r in rows}
    assert at[("n", "A01", "t_a")] == ("2.0", "0.5") and at[("n", "B01", "t_a")][0] == "2.0"
    # Confluence is 0.1 per cell, so above 0.25 is 3+ cells: A01's 4 and 3 of 2, 4, 3, 1.
    assert at[("c", "A01", "t_a")] == ("0.25", "0.5")


def test_a_cut_is_not_applied_to_the_metric_drawn_in_place_of_the_one_ticked(panel, monkeypatch):
    """With no site intensities on the shared channel, Brightness is drawn as Cell count; the Brightness cut was
    chosen for brightness, so the Cell count rows carry no fraction rather than one counted at it."""
    p, ctx, tick, out = panel
    tick("brightness")
    p._threshold_edits["brightness"].setText("150")
    ctx.state.channel.active_channel = "mcherry"
    p._run_batch()
    rows = list(csv.DictReader(next(out.rglob("*_summary.csv")).open()))
    drawn = [r for r in rows if r.get("requested_metric") == "brightness"]
    assert drawn and {r["metric"] for r in drawn} == {"n"}
    assert {(r["responding_above"], r["fraction_responding"]) for r in drawn} == {("", "")}
