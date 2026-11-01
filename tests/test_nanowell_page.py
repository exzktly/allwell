"""The Nanowells page driven through its real builder, worker and canvas, in a minimal shell (tracker V1b,
V3b, V3d, V3e, V3, V3a; CLAUDE.md §7.6).

``tests/_minimal_shell.py`` gives a production ``ViewerContext`` without a ``ReviewApp``, so these run in the
fast lane. The page is built with ``build_nanowell_tab``, the controls are the real widgets, the load runs on
the real worker bridge, and clicks are real mouse presses on the canvas. Numbers are read back from the
figure's text artists, not from the model the figure was drawn from (CLAUDE.md §7.4 #1).
"""

from __future__ import annotations

import json
import re
import time

import pandas as pd
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from review.features.nanowell import controller as nwc  # noqa: E402
from tests.test_nanowell_review_data import STARTS, _dataset, _row  # noqa: E402

WELLS = ("A01", "A02", "B01")
#: Per well, per timepoint, cells in each of four nanowells; None is occluded.
COUNTS = {"A01": {"t_c": [1, 2, 0, None], "t_a": [2, 4, 3, 1]},
          "A02": {"t_c": [1, 1, 1, 1], "t_a": [2, 2, 2, 2]},
          "B01": {"t_c": [3, 0, 1, 1], "t_a": [3, None, 1, 2]}}


def _write_nanowell_dataset(tmp_path, *, scheme: int | None = None, counts: dict | None = None):
    plate = counts or COUNTS
    wells, cells = {}, []
    for well, by_tp in plate.items():
        rows = []
        for tp, counts in by_tp.items():
            for q, n in enumerate(counts):
                if n is None:
                    rows.append(_row(tp, q, 0, state="occluded"))
                    continue
                rows.append(_row(tp, q, 0, n=n, c=0.1 * n, gfp=100.0 * n, state="empty" if n == 0 else "measured"))
                cells += [{"well": well, "fov": "F0", "timepoint": tp, "nucleus_id": len(cells) + 1, "area_px": 50,
                           "GFP_mean_intensity": 10.0 + q, "nw_q": q, "nw_r": 0} for _ in range(n)]
        wells[well] = rows
    out = _dataset(tmp_path, wells, starts={k: STARTS[k] for k in sorted({t for c in plate.values() for t in c})})
    frame = pd.DataFrame(cells)
    for well in plate:
        frame[frame["well"] == well].to_csv(out / f"measurements_{well}.csv", index=False)
    info = json.loads((out / "pipeline_info.json").read_text())
    info["nanowell"]["scheme_version"] = scheme or info["nanowell"]["scheme_version"]
    (out / "pipeline_info.json").write_text(json.dumps(info))
    return out


def _page(qtbot, path):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.features.nanowell.tab_view import build_nanowell_tab
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features   # as ReviewApp sets them
    load_dataset(ctx, path)
    ctx.state.channel.active_channel = "gfp"
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    build_nanowell_tab(ctx, frame)
    window.resize(1500, 900)
    window.show()
    qtbot.waitExposed(window)
    return ctx


@pytest.fixture
def page(qtbot, tmp_path):
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    ctx.state.selection.selected_wells = {"A01", "B01"}
    return ctx


def _loaded(qtbot, ctx):
    nwc.nw_on_activate(ctx)
    st = ctx.state.feature_slice("nanowell")
    qtbot.waitUntil(lambda: st.metrics is not None and not st.computing, timeout=20000)
    return st


def _texts(ctx, gid=None):
    fig = ctx.widgets.get("_nw_fig")
    found = list(fig.texts) + [t for ax in fig.axes for t in ax.texts]
    return [t.get_text() for t in found if gid is None or t.get_gid() == gid]


# ── V1b: states ──────────────────────────────────────────────────────────────
def test_with_no_wells_selected_the_page_says_which_wells_have_nanowell_tables(qtbot, tmp_path):
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    st = _loaded(qtbot, ctx)
    # Nothing is selected, so no well's table is read; the folder listing still names every well that has one.
    assert (st.data.wells, st.data.available) == ([], list(WELLS))
    reason = nwc.nanowell_empty_reason(ctx)
    assert reason.title == "No nanowell wells selected." and "A01, A02, B01" in reason.hint
    assert not ctx.widgets.get("_nw_canvas").isVisible()


def test_a_dataset_without_nanowell_tables_says_so_rather_than_drawing_nothing(qtbot, tmp_path):
    out = _write_nanowell_dataset(tmp_path)
    for path in (out / "nanowells").iterdir():
        path.unlink()
    (out / "nanowells").rmdir()
    ctx = _page(qtbot, out)
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded_or_none(qtbot, ctx)
    assert st.data is None and nwc.nanowell_empty_reason(ctx).title == "This dataset has no nanowell tables."


def _loaded_or_none(qtbot, ctx):
    nwc.nw_on_activate(ctx)
    st = ctx.state.feature_slice("nanowell")
    qtbot.waitUntil(lambda: st.loaded_for is not None and not st.computing, timeout=20000)
    return st


def test_a_scheme_this_version_cannot_read_is_named_on_the_page(qtbot, tmp_path):
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path, scheme=9))
    ctx.state.selection.selected_wells = {"A01"}
    _loaded_or_none(qtbot, ctx)
    reason = nwc.nanowell_empty_reason(ctx)
    assert reason.title == "This dataset's nanowell tables cannot be read." and "scheme version 9" in reason.hint


def test_the_load_runs_off_the_event_loop_and_a_stale_result_never_replaces_a_newer_one(page, qtbot, monkeypatch):
    import review.features.nanowell.controller as controller

    real = controller.site_metrics
    calls = []

    def slow(data, baseline, gate=None, focus=None):
        if baseline == "earliest":
            time.sleep(1.5)                       # the first request is the slow one, so it arrives last
        calls.append(baseline)
        return real(data, baseline, gate, focus)

    monkeypatch.setattr(controller, "site_metrics", slow)
    st = ctx_state = page.state.feature_slice("nanowell")
    started = time.monotonic()
    nwc.nw_on_activate(page)
    assert time.monotonic() - started < 1.0 and st.computing      # returned at once; the worker has it
    baseline_cb = page.widgets.get("_nw_baseline_cb")
    baseline_cb.setCurrentIndex(baseline_cb.findData("first_measurable"))
    qtbot.waitUntil(lambda: not ctx_state.computing and ctx_state.metrics is not None, timeout=20000)
    qtbot.wait(1800)                                                # let the slow, stale result arrive
    assert st.loaded_for[1] == "first_measurable"
    assert calls == ["first_measurable", "earliest"]                # the stale result did arrive, last
    assert st.loaded_for == (str(page.state.dataset.data_dir), "first_measurable")


# ── V3b / V3e: the figure accounts for every nanowell ────────────────────────
def test_the_figure_s_own_numbers_add_up_to_the_roster(page, qtbot):
    _loaded(qtbot, page)
    used = [int(t.split("= ")[1].replace(",", "")) for t in _texts(page, "nw-used")]
    excluded = [int(t.split("excl. ")[1].replace(",", "")) for t in _texts(page, "nw-excluded")]
    footer = next(t for t in _texts(page, "nw-footer-used"))
    match = re.match(r"Nanowell × timepoint values used ([\d,]+) of ([\d,]+) · excluded ([\d,]+): (.*) · wells (\d+)",
                     footer)
    assert match, footer
    u, roster, e = (int(match.group(i).replace(",", "")) for i in (1, 2, 3))
    assert (sum(used), sum(excluded)) == (u, e) == (14, 2) and roster == 16 and u + e == roster
    assert match.group(4) == "occluded 2" and match.group(5) == "2"
    assert page.widgets.get("_nw_canvas").isVisible()                          # the figure, not a placeholder
    assert any("packed within ±0.4" in t for t in _texts(page, "nw-footer"))


def test_choosing_a_metric_in_the_real_combo_redraws_with_its_label(page, qtbot):
    _loaded(qtbot, page)
    combo = page.widgets.get("_nw_metric_cb")
    combo.setCurrentIndex(combo.findData("confluence_fc"))
    fig = page.widgets.get("_nw_fig")
    assert fig.axes[0].get_ylabel() == "Confluence fold change"
    footer = next(t for t in _texts(page, "nw-footer-used"))
    assert footer == ("Nanowell × timepoint values used 10 of 16 · excluded 6: no confluence at baseline 3 · "
                      "occluded 2 · baseline occluded 1 · wells 2")


def test_a_shared_channel_the_tables_lack_falls_back_to_the_page_s_channel_rather_than_greying(page, qtbot):
    """This test once asserted that a shared channel the tables lack greys Brightness. That was the reported
    defect: the shared selector is not on this page, so nothing here could un-grey it ("there is no channel selector
    ... EGFP shows up nowhere"). The page now draws its own Channel, its first measured one by default."""
    _loaded(qtbot, page)
    page.state.channel.active_channel = "mcherry"
    nwc.nw_on_channel_changed(page)
    combo = page.widgets.get("_nw_metric_cb")
    assert combo.model().item(combo.findData("brightness")).isEnabled()
    assert page.widgets.get("_nw_channel_cb").currentData() == "GFP"


def test_a_channel_metric_is_greyed_when_the_run_measured_no_fluorescence(qtbot, tmp_path):
    out = _write_nanowell_dataset(tmp_path)
    for path in (out / "nanowells").glob("*.csv"):
        pd.read_csv(path).drop(columns=["GFP_integrated", "GFP_mean_bg"]).to_csv(path, index=False)
    ctx = _page(qtbot, out)
    ctx.state.selection.selected_wells = {"A01"}
    _loaded(qtbot, ctx)
    combo = ctx.widgets.get("_nw_metric_cb")
    item = combo.model().item(combo.findData("brightness"))
    assert not item.isEnabled() and "measured no fluorescence" in item.toolTip()
    assert ctx.widgets.get("_nw_channel_cb").count() == 0


def test_along_wells_draws_one_timepoint_with_the_statistic_over_wells(page, qtbot):
    _loaded(qtbot, page)
    axis = page.widgets.get("_nw_axis_seg")
    axis.setCurrentIndex(1)
    st = page.state.feature_slice("nanowell")
    assert st.axis == "wells" and st.model.timepoint == "t_a"
    assert page.widgets.get("_nw_tp_cb").isVisible() and page.widgets.get("_nw_control_cb").isVisible()
    # No control chosen: nothing per-well is drawn beside the swarms, so the footer claims no interval.
    assert not any("t interval" in t for t in _texts(page, "nw-footer"))


# ── V3 / V3a: clicking a dot opens its history ───────────────────────────────
def _click_dot(qtbot, ctx, site, modifier=Qt.NoModifier):
    """Click where *site*'s dot is drawn — its position read from the scatter on the figure, checked against
    the hit map, so a hit map that disagrees with the drawing cannot pass."""
    import numpy as np

    st = ctx.state.feature_slice("nanowell")
    canvas = ctx.widgets.get("_nw_canvas")
    drawn = np.concatenate([c.get_offsets() for ax in ctx.widgets.get("_nw_fig").axes for c in ax.collections
                            if c.get_gid() == "nw-dots"])
    for ax, xs, ys, sites in st.hits.entries:
        for x, y, s in zip(xs, ys, sites):
            if tuple(s) == tuple(site):
                assert np.min(np.hypot(drawn[:, 0] - x, drawn[:, 1] - y)) < 1e-9, "hit map is not the drawing"
                px, py = ax.transData.transform((x, y))
                ratio = canvas.devicePixelRatioF()
                QTest.mouseClick(canvas, Qt.LeftButton, modifier,
                                 QPoint(int(round(px / ratio)), int(round(canvas.height() - py / ratio))))
                return
    raise AssertionError(f"{site} is not drawn")


def test_clicking_a_dot_selects_that_nanowell_and_shift_click_adds_another(page, qtbot):
    _loaded(qtbot, page)
    before = set(page.state.selection.selected_wells)
    _click_dot(qtbot, page, ("A01", 1, 0))
    st = page.state.feature_slice("nanowell")
    assert st.selected_sites == [["A01", 1, 0]]
    assert page.widgets.get("_nw_detail_pane").isVisible()
    detail_texts = [t.get_text() for t in page.widgets.get("_nw_detail_fig").texts]
    assert detail_texts == ["A01 · q 1, r 0 — measured at every timepoint"]
    _click_dot(qtbot, page, ("B01", 0, 0), Qt.ShiftModifier)
    assert st.selected_sites == [["A01", 1, 0], ["B01", 0, 0]]
    assert set(page.state.selection.selected_wells) == before                  # not a plate selection


def test_clicking_empty_space_clears_the_selection_and_hides_the_detail(page, qtbot):
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    canvas = page.widgets.get("_nw_canvas")
    ax = page.widgets.get("_nw_fig").axes[0]
    px, py = ax.transAxes.transform((0.02, 0.98))
    ratio = canvas.devicePixelRatioF()
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier,
                     QPoint(int(px / ratio), int(canvas.height() - py / ratio)))
    assert page.state.feature_slice("nanowell").selected_sites == []
    assert not page.widgets.get("_nw_detail_pane").isVisible()


@pytest.mark.parametrize("view", ["trajectories", "position"])
def test_a_click_on_a_view_with_no_dots_clears_the_selection(page, qtbot, view):
    """Clear selection was removed on the promise that clicking empty space clears it "on every view". Trajectories
    and Position draw no clickable nanowells, so the click returned before clearing, and a selection made on
    Distribution — and the detail it opens — could not be cleared from those views' figures."""
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    st = page.state.feature_slice("nanowell")
    assert st.selected_sites
    nwc.set_view(page, view)
    qtbot.waitUntil(lambda: st.view == view and not st.computing, timeout=10000)
    canvas = page.widgets.get("_nw_canvas")
    ax = page.widgets.get("_nw_fig").axes[0]
    px, py = ax.transAxes.transform((0.5, 0.5))
    ratio = canvas.devicePixelRatioF()
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier,
                     QPoint(int(px / ratio), int(canvas.height() - py / ratio)))
    assert st.selected_sites == []
    assert not page.widgets.get("_nw_detail_pane").isVisible()


def test_an_occluded_timepoint_is_a_gap_in_the_detail_not_a_zero(page, qtbot):
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("B01", 1, 0))                          # 0 cells, then occluded
    fig = page.widgets.get("_nw_detail_fig")
    line = fig.axes[0].get_lines()[0]
    ys = list(line.get_ydata())
    assert ys[0] == 0.0 and pd.isna(ys[1])
    assert [t.get_text() for t in fig.texts] == ["B01 · q 1, r 0 — t_c: empty; t_a: occluded"]


# ── what the review of PR 10a found ──────────────────────────────────────────
def test_changing_the_baseline_and_back_never_lands_the_stale_result(page, qtbot, monkeypatch):
    """Earliest loaded → First measurable (slow) → Earliest again: the slow result must not be applied."""
    import review.features.nanowell.controller as controller

    st = _loaded(qtbot, page)
    real = controller.site_metrics
    monkeypatch.setattr(controller, "site_metrics",
                        lambda data, baseline, *rest: (time.sleep(1.2), real(data, baseline, *rest))[1])
    baseline_cb = page.widgets.get("_nw_baseline_cb")
    baseline_cb.setCurrentIndex(baseline_cb.findData("first_measurable"))
    baseline_cb.setCurrentIndex(baseline_cb.findData("earliest"))
    qtbot.wait(1800)
    assert st.baseline == "earliest" and st.loaded_for[1] == "earliest" and not st.computing
    footer = next(t for t in _texts(page, "nw-footer-used"))
    assert footer.startswith("Nanowell × timepoint values used 14 of 16")


def test_the_baseline_control_offers_every_timepoint_and_choosing_one_measures_from_it(page, qtbot):
    """Through the real Baseline combo: the two rules, then t_c and t_a. From t_a, A01's nanowells q 0 and q 1 (1 and
    2 cells at t_c, 2 and 4 at t_a) read 0.5 at t_c — the earliest and first-measurable rules both read 1.0 there."""
    st = _loaded(qtbot, page)
    st.metric = "count_fc"
    baseline_cb = page.widgets.get("_nw_baseline_cb")
    offered = [baseline_cb.itemData(i) for i in range(baseline_cb.count())]
    assert offered == ["earliest", "first_measurable", "tp:t_c", "tp:t_a"]
    baseline_cb.setCurrentIndex(baseline_cb.findData("tp:t_a"))
    qtbot.waitUntil(lambda: st.loaded_for[1] == "tp:t_a" and not st.computing, timeout=20000)
    assert st.baseline == "tp:t_a"
    a01 = st.metrics[(st.metrics["well"] == "A01") & st.metrics["nw_q"].isin([0, 1])]
    assert a01.groupby("timepoint")["count_fc"].apply(set).to_dict() == {"t_c": {0.5}, "t_a": {1.0}}
    nwc.redraw(page)
    legend = " ".join(_texts(page, "nw-legend"))
    assert "Cells at baseline (t_a)" in legend
    assert baseline_cb.currentData() == "tp:t_a"


def test_a_baseline_changed_without_a_load_is_fetched_before_it_is_drawn(page, qtbot):
    """A reset or a restore writes the baseline straight onto the state; the next draw must not use stale
    fold changes for it."""
    st = _loaded(qtbot, page)
    st.apply_config({"baseline": "first_measurable", "metric": "count_fc"})
    nwc.redraw(page)
    qtbot.waitUntil(lambda: st.loaded_for[1] == "first_measurable" and not st.computing, timeout=20000)
    assert set(st.metrics["baseline_timepoint"].dropna()) <= {"t_c", "t_a"}
    legend = " ".join(_texts(page, "nw-legend"))
    assert "Cells at baseline (first measurable)" in legend


def test_the_figure_keys_the_occupancy_colours_and_counts_the_wells(page, qtbot):
    _loaded(qtbot, page)
    legend = _texts(page, "nw-legend")
    assert legend[0] == "Cells at baseline (earliest):" and legend[1:] == ["0", "1", "2", "3+", "?"]


def test_a_capture_paints_the_dots_and_the_ink_for_white(page, qtbot):
    """Save / Copy flip the card to publication without a redraw: the dots must take the publication ramp."""
    from matplotlib.colors import to_hex

    from ui.theme import Colors, CPub

    _loaded(qtbot, page)
    card = page.widgets.get("_nw_card")
    fig = page.widgets.get("_nw_fig")
    dots = [c for ax in fig.axes for c in ax.collections if c.get_gid() == "nw-dots"]
    on_screen = {to_hex(c) for coll in dots for c in coll.get_edgecolors()}
    assert on_screen <= {c.lower() for c in Colors.occupancy_ramp + (Colors.occupancy_unknown,)}
    with card._publication_capture():
        captured = {to_hex(c) for coll in dots for c in coll.get_edgecolors()}
        footer = next(t for t in fig.texts if t.get_gid() == "nw-footer-used")
        assert to_hex(footer.get_color()) != to_hex(Colors.text_primary) or CPub.bg != Colors.plot_bg
    assert captured <= {c.lower() for c in CPub.occupancy_ramp + (Colors.occupancy_unknown,)}
    assert captured != on_screen


def test_a_selected_well_without_a_nanowell_table_is_named(page, qtbot):
    page.state.dataset.well_paths["C09"] = page.state.dataset.well_paths["A01"]
    page.state.selection.selected_wells = {"A01", "C09"}
    _loaded(qtbot, page)
    nwc.redraw(page)
    assert any("C09" in t and "without a nanowell table" in t for t in _texts(page, "nw-footer"))


def test_the_page_reloads_after_a_dataset_switch_even_from_its_empty_state(qtbot, tmp_path):
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path / "a"))
    st = _loaded(qtbot, ctx)                                     # no wells selected: the empty state shows
    assert not ctx.widgets.get("_nw_canvas").isVisible()
    from tests._minimal_shell import load_dataset

    load_dataset(ctx, _write_nanowell_dataset(tmp_path / "b"))
    nwc.nw_on_dataset_loaded(ctx)
    qtbot.waitUntil(lambda: st.loaded_for is not None and not st.computing, timeout=20000)
    assert st.loaded_for[0] == str(ctx.state.dataset.data_dir)


@pytest.mark.parametrize("hidden, solo, expected", [
    (True, set(), ["A01", "B01"]),      # every set hidden: the plate selection, as on every other tab
    (False, {"B01"}, ["A02", "B01"]),   # a visible set plus a toggled solo well; the selection is ignored
])
def test_the_page_draws_the_wells_every_plotting_tab_draws(page, hidden, solo, expected):
    """The page's wells come from ``plot_group_well_lists``, not a rule of its own (roll-up review X4): a rule of
    its own drew nothing with every set hidden while the other tabs drew the plate selection."""
    from review.data.data_loading import plot_group_well_lists

    sel = page.state.selection
    sel.selections = [{"name": "G", "wells": ["A02"], "hidden": hidden}]
    sel.active_solo_wells = solo
    assert nwc.active_wells(page) == expected
    assert sorted(w for _n, ws, _r in plot_group_well_lists(page, fallback_to_all=False) for w in ws) == expected


def test_saving_while_another_baseline_is_being_computed_is_refused_and_says_so(page, qtbot, monkeypatch):
    """Roll-up review X12: the file labelled the metrics with the baseline just chosen while they were still the
    previous one's. Saving then is refused with the reason; once the page has them, it writes."""
    import pandas as pd

    import review.core.csv_export as csv_export

    st = _loaded(qtbot, page)
    st.apply_config({"baseline": "first_measurable"})          # chosen; the metrics are still the earliest's
    from pathlib import Path

    out = str(Path(page.state.dataset.data_dir) / "export.csv")
    monkeypatch.setattr(csv_export, "ask_save_csv", lambda *_a, **_k: out)
    statuses = []
    monkeypatch.setattr(page.notify, "set_status", lambda text, *a, **k: statuses.append(text))
    nwc.export_page_csv(page)
    assert statuses[-1].startswith("The nanowell metrics for baseline 'first_measurable' are still being computed")
    nwc.redraw(page)
    qtbot.waitUntil(lambda: st.loaded_for[1] == "first_measurable" and not st.computing, timeout=20000)
    nwc.export_page_csv(page)
    written = pd.read_csv(out)
    assert set(written["baseline"]) == {"first_measurable"}
    assert set(written["baseline_timepoint"].dropna()) == set(st.metrics["baseline_timepoint"].dropna())


def test_along_wells_the_timepoint_named_is_the_one_drawn(qtbot, tmp_path):
    """Roll-up review X17: with no timepoint chosen the combo named the dataset's latest (t_d, which only B01 has)
    while the figure drew the drawn wells' latest (t_a). Both now come from the drawn wells."""
    counts = {"A01": {"t_c": [1, 2, 0, 1], "t_a": [2, 4, 3, 1]},
              "B01": {"t_c": [3, 0, 1, 1], "t_a": [3, 1, 1, 2], "t_d": [1, 1, 1, 1]}}
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path, counts=counts))
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded(qtbot, ctx)
    ctx.widgets.get("_nw_axis_seg").setCurrentIndex(1)
    assert st.timepoint in ("", None)
    assert (nwc.shown_timepoint(ctx)[1], st.model.timepoint, ctx.widgets.get("_nw_tp_cb").currentData()) == \
        ("t_a", "t_a", "t_a")


def test_a_percentage_typed_into_the_rim_focus_box_is_taken(qtbot, page):
    """Typing a percentage into the band's rim focus box sets it, with the cursor where a click leaves it. The box once
    carried a " % of sharp" suffix, and Qt's spin box refuses typing and Backspace with the cursor inside a suffix,
    so a click on most of the field ignored the value typed (the reported bug)."""
    from PySide6.QtCore import Qt

    _loaded(qtbot, page)
    spin = page.widgets.get("_nw_rim_focus_spin")
    seen = []
    spin.valueChanged.connect(seen.append)
    edit = spin.lineEdit()
    assert edit.text() == "75"                       # the number alone: nothing in the field refuses the cursor
    edit.setFocus()
    edit.setCursorPosition(len(edit.text()))         # where a click to the right of the number puts it
    qtbot.keyClick(edit, Qt.Key_Backspace)
    qtbot.keyClick(edit, Qt.Key_Backspace)
    qtbot.keyClicks(edit, "60")
    qtbot.keyClick(edit, Qt.Key_Return)
    assert (edit.text(), spin.value(), seen) == ("60", 60, [60])
    assert page.state.qc.params.nanowell_focus_fraction == 0.6
    st = page.state.feature_slice("nanowell")
    qtbot.waitUntil(lambda: st.focus_for == (True, 0.6) and not st.computing, timeout=20000)
    timer, canvas = page.widgets.get("_nw_refit_timer"), page.widgets.get("_nw_canvas")
    qtbot.waitUntil(lambda: not (timer is not None and timer.isActive())
                    and not getattr(canvas, "_draw_pending", False), timeout=5000)


def test_a_rim_focus_percentage_being_typed_survives_a_redraw_and_a_click_on_the_figure_applies_it(qtbot, page):
    """Reported: "changes to focus threshold in array do not have an effect". The box commits on Enter or focus-out,
    and every redraw refreshed the band, whose ``setValue`` rewrote the field with the rule in force — so on the Array
    view, which redraws as each tile arrives, the number typed was put back before it was ever committed. And a click
    on the figure did not end the edit either: the canvas took no focus, so the box kept it, uncommitted."""
    from PySide6.QtCore import QPoint, Qt

    st = _loaded(qtbot, page)
    spin, canvas = page.widgets.get("_nw_rim_focus_spin"), page.widgets.get("_nw_canvas")
    edit = spin.lineEdit()
    qtbot.mouseClick(edit, Qt.LeftButton)
    edit.selectAll()
    qtbot.keyClicks(edit, "60")
    nwc.redraw(page)                                    # a tile arriving, a refit: anything that redraws
    assert edit.text() == "60"                          # what was typed is still there
    qtbot.mouseClick(canvas, Qt.LeftButton, pos=QPoint(40, 40))
    assert page.state.qc.params.nanowell_focus_fraction == 0.6
    qtbot.waitUntil(lambda: st.focus_for == (True, 0.6) and not st.computing, timeout=20000)
    timer = page.widgets.get("_nw_refit_timer")
    qtbot.waitUntil(lambda: not (timer is not None and timer.isActive())
                    and not getattr(canvas, "_draw_pending", False), timeout=5000)


def test_every_rim_focus_change_applies_however_the_box_is_left(qtbot, page):
    """Reported: "The first change to the field worked. Subsequent changes did not." The box commits on Enter or
    focus-out, and a click on a canvas, a tool button or a tab bar ends no edit — nor does anything in a window that
    is not the active one, which is where the first change's application-modal progress popup can leave it. So a
    number typed and then left was never applied. It now also commits after a pause in typing. Here, as for the
    reporter, the first change's popup leaves the window inactive (measured under the offscreen platform), so the
    later changes have no focus-out to commit them."""
    from PySide6.QtCore import Qt

    from widgets.spin_box import TYPING_PAUSE_MS

    st = _loaded(qtbot, page)
    spin = page.widgets.get("_nw_rim_focus_spin")
    edit = spin.lineEdit()
    edit.selectAll()
    qtbot.keyClicks(edit, "60")
    qtbot.keyClick(edit, Qt.Key_Return)                               # the first change, with Enter
    qtbot.waitUntil(lambda: st.focus_for == (True, 0.6) and not st.computing, timeout=20000)
    for typed, fraction in (("50", 0.5), ("80", 0.8)):               # then typed and left: no Enter, no focus-out
        edit.selectAll()
        qtbot.keyClicks(edit, typed)
        qtbot.wait(TYPING_PAUSE_MS // 2)
        assert page.state.qc.params.nanowell_focus_fraction != fraction      # not while still typing
        qtbot.waitUntil(lambda: page.state.qc.params.nanowell_focus_fraction == fraction, timeout=5000)
        qtbot.waitUntil(lambda: st.focus_for == (True, fraction) and not st.computing, timeout=20000)
    canvas, timer = page.widgets.get("_nw_canvas"), page.widgets.get("_nw_refit_timer")
    qtbot.waitUntil(lambda: not (timer is not None and timer.isActive())
                    and not getattr(canvas, "_draw_pending", False), timeout=5000)


def _key_texts(ctx):
    fig = ctx.widgets.get("_nw_fig")
    return [t.get_text() for ax in fig.axes for a in ax.artists if a.get_gid() == "nw-array-key"
            for t in a.get_texts()]


def test_the_array_footer_is_short_until_notes_is_turned_on(qtbot, page):
    """Reported: "The text at the bottom of the array tab take up way too much space". With *Notes* off (the default)
    the footer is the page's note, the counts — in the key's own groups, adding up to the roster (UX review N1) — and
    what went wrong. Notes, on the band beside Overlays as well as in the Layers menu (N6), puts how to read the figure
    in a box over the foot of the array, so the array neither moves nor shrinks (N3), and says only what applies to
    what is drawn: no fused sentence with no fused nanowell, no plain "ok" grid status (N4)."""
    _loaded(qtbot, page)
    nwc.set_view(page, "array")
    st = page.state.feature_slice("nanowell")
    assert not st.layers.notes and not _texts(page, "nw-array-notes")
    short = _texts(page, "nw-array-footer")
    assert "4 nanowells: 3 with a value · 1 occluded" in short
    assert not any(t.startswith(("Measured positions", "Filled by")) or t.endswith(": ok.") for t in short)
    where = tuple(st.drawn.ax.get_position().bounds)
    button = page.widgets.get("_nw_notes_btn")
    button.click()                                                     # the band's button
    assert st.layers.notes and page.widgets.get("_nw_layer_notes").isChecked()
    assert _texts(page, "nw-array-footer") == short                    # the footer itself is unchanged
    assert tuple(st.drawn.ax.get_position().bounds) == pytest.approx(where)
    (notes,) = _texts(page, "nw-array-notes")
    assert notes.splitlines() == [
        "Measured positions in the well frame (the square is the central nanowell).",
        "Filled by Cell count. A grey outline has no value: its hatch or dash names its state, and with Values on the "
        "words inside it say why (the key above the array lists each)."]
    nwc.apply_array_preset(page, "qc")                                 # a preset keeps the reader's choice
    assert st.layers.notes and button.isChecked()
    page.widgets.get("_nw_layer_notes").trigger()                      # and the menu entry turns both off
    assert not st.layers.notes and not button.isChecked() and not _texts(page, "nw-array-notes")


def test_a_nanowell_with_cells_and_no_fold_change_says_why_on_the_array(qtbot, page):
    """Asked: "what is the meaning of the nanowell status no value? I see clearly identifiable cells in them". At t_a,
    A01's nanowell (2, 0) holds 3 cells and (3, 0) holds 1, but at the baseline t_c (2, 0) held none and (3, 0) was
    occluded: both count fold changes are undefined. Each says which inside itself, and the key names both (UX
    review B1: the two could not be told apart on the array)."""
    _loaded(qtbot, page)
    nwc.set_view(page, "array")
    nwc._set(page, "metric", "count_fc")
    nwc.set_timepoint(page, "t_a")
    st = page.state.feature_slice("nanowell")
    assert nwc.page_well(page) == "A01" and st.metric == "count_fc"
    keyed = _key_texts(page)
    assert "no value: baseline occluded (base occl.) · 1" in keyed
    assert "no value: no cells at baseline (0 at base) · 1" in keyed
    labels = dict(zip([s[1:] for s in st.drawn.sites.sites], _texts(page, "nw-array-address")))
    assert (labels[(2, 0)].splitlines()[-1], labels[(3, 0)].splitlines()[-1]) == ("0 at base", "base occl.")


def test_the_rim_focus_control_on_the_band_changes_the_qc_layer_s_rule_and_the_metrics_follow(qtbot, page):
    """The band's *Rim focus* box and percentage are a mirror of the QC layer's switch: they change the dataset's
    QC parameters, and the page recomputes with the rule: the metrics on hand are stamped with the rule they were
    computed for (``focus_for``)."""
    from review.domain import nanowell_rim_focus as rfc

    st = _loaded(qtbot, page)
    assert st.focus_for == rfc.RimFocusRule().key()
    box, spin = page.widgets.get("_nw_rim_focus_box"), page.widgets.get("_nw_rim_focus_spin")
    assert box.isChecked() and spin.value() == 75
    box.setChecked(False)
    qtbot.waitUntil(lambda: st.focus_for == (False,) and not st.computing, timeout=20000)
    assert not page.state.qc.params.exclude_soft_nanowells and not spin.isEnabled()
    box.setChecked(True)
    spin.setValue(60)
    qtbot.waitUntil(lambda: st.focus_for == (True, 0.6) and not st.computing, timeout=20000)
    assert page.state.qc.params.nanowell_focus_fraction == 0.6 and page.state.qc.params.exclude_soft_nanowells
    assert not any("focus" in key for key in st.to_config())        # the QC layer's setting, not the page's
    # The recompute ends in a redraw that matplotlib queues on the canvas; ending the test inside that window leaves
    # the draw to fire on a canvas the widget reaper has deleted ("Internal C++ object (FigureCanvasQTAgg) already
    # deleted" at teardown, seen in CI). Wait it out, as test_nanowell_page_starting_cells._settled does.
    timer, canvas = page.widgets.get("_nw_refit_timer"), page.widgets.get("_nw_canvas")
    qtbot.waitUntil(lambda: not (timer is not None and timer.isActive())
                    and not getattr(canvas, "_draw_pending", False), timeout=5000)


def test_a_recompute_that_finishes_after_the_page_is_deleted_touches_no_widget(qtbot, page):
    """A recompute runs off the UI thread, and its result can arrive after the page is gone (the window closed, or a
    test's widgets reaped). It used to refresh the band's controls and redraw regardless, and raised
    ``Internal C++ object (PySide6.QtWidgets.QTabBar) already deleted`` from ``tab_view.refresh_controls`` — seen as a
    teardown error in CI. The result still lands in the page's state; only the widgets are left alone."""
    import shiboken6

    st = _loaded(qtbot, page)
    box = page.widgets.get("_nw_rim_focus_box")
    box.setChecked(False)                                           # the QC layer's update, then a recompute
    qtbot.waitUntil(lambda: st.computing, timeout=20000)            # off the UI thread
    shiboken6.delete(page.window.centralWidget())                   # the page goes while it runs
    qtbot.waitUntil(lambda: not st.computing, timeout=20000)        # its result arrives with no page to show it
    assert st.focus_for == (False,) and st.metrics is not None
