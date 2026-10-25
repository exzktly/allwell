"""The Extremes montage laid out to fill its figure, the timepoints it shows chosen, and each crop's metrics written
on it (and under each panel of the pop-out).

Asked for: the montage left so much of the figure empty that the crops were too small to use; the timepoints
should sit side by side, only those chosen, using the space; and the crops should show the nanowell's other
computed metrics. Then: the text and metrics took too much of the space themselves. So every word a crop carries
is written inside it — the fold change in its corner, one line of metrics along its foot — and the crops take the
whole figure. The layout is decided in inches by :func:`renderer._extremes_layout`, so the cases here hand it
figures of known size and check the arrangement and crop side it reaches; the page tests read the figure back.
"""

from __future__ import annotations

import math

import pytest
from matplotlib.figure import Figure

from review.features.nanowell import renderer
from review.features.nanowell.series_compute import compact_number, crop_metric_lines

ROW = {"state": "measured", "n": 6.0, "c": 0.24, "brightness_GFP": 10.0, "signal_GFP": 5790.0}


# ── the metrics under a crop ─────────────────────────────────────────────────
def test_a_measured_crop_is_captioned_with_its_count_confluence_brightness_and_signal():
    assert crop_metric_lines(ROW, "GFP") == ["6 cells", "24% conf.", "signal 5.79k", "bright 10"]
    assert crop_metric_lines({**ROW, "n": 1.0}, None) == ["1 cell", "24% conf."]       # no channel: no intensities


def test_a_missing_measurement_is_left_out_and_a_state_without_numbers_is_named():
    assert crop_metric_lines({**ROW, "c": math.nan, "signal_GFP": None}, "GFP") == ["6 cells", "bright 10"]
    assert crop_metric_lines({"state": "occluded", "n": None}, "GFP") == ["occluded"]
    assert crop_metric_lines({**ROW, "state": "empty", "n": 0.0, "c": 0.0}, None) == ["0 cells", "0% conf."]


@pytest.mark.parametrize("value, text", [(5790.0, "5.79k"), (812.0, "812"), (2.5e6, "2.5M"), (-1234.0, "-1.23k"),
                                         (0.0123, "0.0123")])
def test_big_numbers_are_written_short(value, text):
    assert compact_number(value) == text


def test_items_are_packed_into_as_few_lines_as_the_width_holds():
    items = ["6 cells", "24% conf.", "bright 10", "signal 5.79k"]
    assert renderer._pack(items, 400, 6.5) == ["6 cells · 24% conf. · bright 10 · signal 5.79k"]
    assert renderer._pack(items, 80, 6.5) == ["6 cells · 24% conf.", "bright 10", "signal 5.79k"]
    assert renderer._pack(items, 10, 6.5) == items                              # one each when nothing fits


def test_a_crop_s_line_keeps_the_measurements_that_fit_in_order_and_counts_the_rest():
    items = ["6 cells", "24% conf.", "signal 5.79k", "bright 10"]
    assert renderer._one_line(items, 400, 6.5) == ("6 cells · 24% conf. · signal 5.79k · bright 10", 0)
    assert renderer._one_line(items, 70, 6.5) == ("6 cells · 24% conf.", 2)
    assert renderer._one_line(items, 5, 6.5) == ("", 4)


# ── the layout ───────────────────────────────────────────────────────────────
def _blocks(n=5):
    return [("Largest", [object()] * n), ("Smallest", [object()] * n)]


def test_on_a_wide_figure_the_two_blocks_sit_side_by_side_and_the_crops_fill_the_height():
    fig = Figure(figsize=(14.6, 7.4))
    layout = renderer._extremes_layout(fig, _blocks(), 5)
    # Stacked, ten rows share 7.4 in and a crop could be no more than ≈ 0.6 in; side by side, five rows do.
    assert layout.beside and layout.side >= 1.0 and layout.metrics_shown


def test_on_a_tall_figure_the_blocks_stack():
    assert not renderer._extremes_layout(Figure(figsize=(6.0, 16.0)), _blocks(), 5).beside


def test_fewer_timepoints_give_larger_crops():
    fig = Figure(figsize=(11.0, 5.9))
    assert renderer._extremes_layout(fig, _blocks(), 2).side > renderer._extremes_layout(fig, _blocks(), 5).side


def test_crops_too_small_to_hold_their_metrics_write_only_the_fold_change():
    layout = renderer._extremes_layout(Figure(figsize=(6.0, 4.0)), _blocks(), 5)
    assert layout.side < renderer._EXT_MIN_SIDE_WITH_METRICS and not layout.metrics_shown


# ── the page ─────────────────────────────────────────────────────────────────
pytest.importorskip("PySide6")

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell.series_compute import site_series  # noqa: E402
from tests.test_nanowell_page_extremes import _choose_extremes, page  # noqa: E402,F401


def _fig_texts(ctx, gid):
    return [t.get_text() for t in ctx.widgets.get("_nw_fig").texts if t.get_gid() == gid]


def test_the_montage_shows_only_the_timepoints_chosen_and_always_the_ranked_one(page, qtbot):  # noqa: F811
    _choose_extremes(qtbot, page)
    assert _fig_texts(page, "nw-extremes-tp")[:3] == ["t_c", "t_a", "t_d · ranked"]
    menu = page.widgets.get("_nw_extremes_tps_menu")
    page.widgets.get("_nw_extremes_tps_rebuild")()                             # what opening the menu does
    actions = {a.data(): a for a in menu.actions() if a.isCheckable()}
    assert all(a.isChecked() for a in actions.values()) and not actions["t_d"].isEnabled()
    actions["t_a"].setChecked(False)                                           # as a click on it does
    assert page.state.feature_slice("nanowell").extremes_tps == ["t_c", "t_d"]
    assert _fig_texts(page, "nw-extremes-tp")[:2] == ["t_c", "t_d · ranked"]
    assert page.widgets.get("_nw_extremes_tps_btn").text() == "Timepoints: 2 of 3"
    assert any("Timepoints not shown: t_a." in t for t in _fig_texts(page, "nw-extremes-caption"))
    nwc.set_extremes_timepoints(page, [])
    assert _fig_texts(page, "nw-extremes-tp")[:3] == ["t_c", "t_a", "t_d · ranked"]
    assert page.widgets.get("_nw_extremes_tps_btn").text() == "Timepoints: all"


def test_each_crop_is_captioned_with_its_nanowell_s_metrics_at_that_timepoint(page, qtbot):  # noqa: F811
    _choose_extremes(qtbot, page)
    st = page.state.feature_slice("nanowell")
    nwc.set_extremes_timepoints(page, ["t_d"])                                # one column: crops large enough
    first = next(t for t in page.widgets.get("_nw_fig").texts if t.get_gid() == "nw-extremes-row")
    assert first.get_text().startswith("#1  q 0, r 0")
    row = site_series(st.metrics, "A01", 0, 0).set_index("timepoint").loc["t_d"].to_dict()
    expected = crop_metric_lines({**row, "timepoint": "t_d"}, nwc.match_channel("gfp", st.data.channels))
    fig = page.widgets.get("_nw_fig")
    shown = [t for ax in fig.axes for t in ax.texts if t.get_gid() == "nw-extremes-metrics"]
    assert shown and shown[0].axes in page.state.feature_slice("nanowell").extreme_axes   # written on the crop
    side_pt = shown[0].axes.get_position().width * fig.get_size_inches()[0] * 72
    assert shown[0].get_text() == renderer._one_line(expected, side_pt * 0.94, renderer._EXT_METRIC_PT)[0]
    assert shown[0].get_text().startswith(expected[0])


def test_the_pop_out_writes_the_metrics_under_each_panel(page, qtbot):  # noqa: F811
    _choose_extremes(qtbot, page)
    st = page.state.feature_slice("nanowell")
    nwc._select(page, ("A01", 0, 0), False)
    nwc.pop_out_crops(page)
    qtbot.addWidget(page.widgets.get("_nw_popout"))
    fig = page.widgets.get("_nw_popout_fig")
    qtbot.waitUntil(lambda: any(t.get_gid() == "nw-crop-grid-metrics" for ax in fig.axes for t in ax.texts),
                    timeout=10000)
    shown = [t.get_text().replace("\n", " · ") for ax in fig.axes for t in ax.texts
             if t.get_gid() == "nw-crop-grid-metrics"]
    channel = nwc.match_channel("gfp", st.data.channels)
    expected = [" · ".join(crop_metric_lines(row, channel))
                for row in site_series(st.metrics, "A01", 0, 0).to_dict("records")]
    assert shown == expected


def test_the_chosen_timepoints_are_kept_with_the_page_s_settings():
    from review.features.nanowell.state import NanowellState

    st = NanowellState()
    st.apply_config({"extremes_tps": ["t_c", "t_d", 3]})
    assert st.extremes_tps == ["t_c", "t_d"] and st.to_config()["extremes_tps"] == ["t_c", "t_d"]


def test_the_metrics_box_shows_and_hides_the_numbers_on_the_crops(page, qtbot):  # noqa: F811
    from PySide6.QtCore import Qt

    _choose_extremes(qtbot, page)
    nwc.set_extremes_timepoints(page, ["t_d"])                                # one column: crops large enough

    def on_crops(gid):
        return [t.get_text() for ax in page.widgets.get("_nw_fig").axes for t in ax.texts if t.get_gid() == gid]

    assert on_crops("nw-extremes-value") and on_crops("nw-extremes-metrics")    # shown by default
    box = page.widgets.get("_nw_extremes_metrics_box")
    qtbot.waitUntil(lambda: box.isVisible() and box.width() > 0, timeout=5000)
    qtbot.mouseClick(box, Qt.LeftButton)
    st = page.state.feature_slice("nanowell")
    assert st.crop_metrics is False and st.to_config()["crop_metrics"] is False
    assert on_crops("nw-extremes-value") == [] and on_crops("nw-extremes-metrics") == []
    assert _fig_texts(page, "nw-extremes-row")                                 # the ranking is still labelled
    assert len(on_crops("nw-extremes-missing")) == 0 and page.state.feature_slice("nanowell").extreme_axes
    qtbot.mouseClick(box, Qt.LeftButton)
    assert on_crops("nw-extremes-value") and on_crops("nw-extremes-metrics")


def test_the_metrics_setting_defaults_on_and_is_restored():
    from review.features.nanowell.state import NanowellState

    st = NanowellState()
    assert st.crop_metrics is True
    st.apply_config({"crop_metrics": False})
    assert st.crop_metrics is False
    st.apply_config({})
    assert st.crop_metrics is True
