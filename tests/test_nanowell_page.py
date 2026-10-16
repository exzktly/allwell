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
    assert st.data.wells == list(WELLS)
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

    def slow(data, baseline):
        if baseline == "earliest":
            time.sleep(1.5)                       # the first request is the slow one, so it arrives last
        calls.append(baseline)
        return real(data, baseline)

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


def test_a_channel_metric_is_greyed_when_the_shared_channel_has_no_site_intensities(page, qtbot):
    _loaded(qtbot, page)
    page.state.channel.active_channel = "mcherry"
    nwc.nw_on_channel_changed(page)
    combo = page.widgets.get("_nw_metric_cb")
    item = combo.model().item(combo.findData("brightness"))
    assert not item.isEnabled() and "mcherry" in item.toolTip()
    page.state.channel.active_channel = "gfp"
    nwc.nw_on_channel_changed(page)
    assert combo.model().item(combo.findData("brightness")).isEnabled()


def test_along_wells_draws_one_timepoint_with_the_statistic_over_wells(page, qtbot):
    _loaded(qtbot, page)
    axis = page.widgets.get("_nw_axis_seg")
    axis.setCurrentIndex(1)
    st = page.state.feature_slice("nanowell")
    assert st.axis == "wells" and st.model.timepoint == "t_a"
    assert page.widgets.get("_nw_tp_cb").isVisible() and page.widgets.get("_nw_control_cb").isVisible()
    assert any("nanowells are not replicates" in t for t in _texts(page, "nw-footer"))


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
    assert detail_texts == ["A01 · q 1, r 0 — t_c: measured, t_a: measured"]
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


def test_an_occluded_timepoint_is_a_gap_in_the_detail_not_a_zero(page, qtbot):
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("B01", 1, 0))                          # 0 cells, then occluded
    fig = page.widgets.get("_nw_detail_fig")
    line = fig.axes[0].get_lines()[0]
    ys = list(line.get_ydata())
    assert ys[0] == 0.0 and pd.isna(ys[1])
    assert [t.get_text() for t in fig.texts] == ["B01 · q 1, r 0 — t_c: empty, t_a: occluded"]


# ── what the review of PR 10a found ──────────────────────────────────────────
def test_changing_the_baseline_and_back_never_lands_the_stale_result(page, qtbot, monkeypatch):
    """Earliest loaded → First measurable (slow) → Earliest again: the slow result must not be applied."""
    import review.features.nanowell.controller as controller

    st = _loaded(qtbot, page)
    real = controller.site_metrics
    monkeypatch.setattr(controller, "site_metrics",
                        lambda data, baseline: (time.sleep(1.2), real(data, baseline))[1])
    baseline_cb = page.widgets.get("_nw_baseline_cb")
    baseline_cb.setCurrentIndex(baseline_cb.findData("first_measurable"))
    baseline_cb.setCurrentIndex(baseline_cb.findData("earliest"))
    qtbot.wait(1800)
    assert st.baseline == "earliest" and st.loaded_for[1] == "earliest" and not st.computing
    footer = next(t for t in _texts(page, "nw-footer-used"))
    assert footer.startswith("Nanowell × timepoint values used 14 of 16")


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
