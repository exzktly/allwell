"""The Nanowells page's Extremes view: one well's nanowells with the largest and the smallest fold change of the
chosen metric at one timepoint, each a row of its crops over every timepoint.

Asked for: "a summary view of crop montages of the X largest and smallest fold-changes of selected metric over
time" — ranked per well, at the page's timepoint (the latest by default), one row per nanowell. The ranking is
tested on the compute module with values assigned *against* address order, so a view that drew nanowells in
address order, or ranked the raw metric rather than its fold change, would pick different nanowells (CLAUDE.md
§7.8 rule 2). The page half drives the real builder, the real view switch, spin box, timepoint combo and a real
click on the canvas in the minimal shell the other page tests use, and reads the figure's own artists.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell import extremes_compute as ec  # noqa: E402
from review.features.nanowell import image_compute as ic  # noqa: E402
from review.features.nanowell.collect import load_nanowell_data  # noqa: E402
from review.features.nanowell.image_compute import Crop  # noqa: E402
from review.features.nanowell.series_compute import site_metrics  # noqa: E402
from review.features.nanowell.state import NanowellState  # noqa: E402
from tests.test_nanowell_page import _loaded, _page, _write_nanowell_dataset  # noqa: E402

#: A01's eight nanowells (q = 0..7, r = 0). At t_d the count fold changes over t_c are
#: q0 4, q1 0.5, q2 2, q3 1, q4 3, q5 1.5 — out of address order — q6 is seeded later (0 at baseline) and q7 was
#: occluded at baseline. At t_a only q2 has grown (3×), the rest are at 1×, so ranking there picks other nanowells.
COUNTS = {"A01": {"t_c": [2, 2, 2, 2, 2, 2, 0, None], "t_a": [2, 2, 6, 2, 2, 2, 1, 1],
                  "t_d": [8, 1, 4, 2, 6, 3, 5, 1]},
          "B01": {"t_c": [1, 1], "t_a": [1, 1], "t_d": [3, 1]}}


def _qs(rows):
    return [row.site[1] for row in rows]


@pytest.fixture
def metrics(tmp_path):
    data = load_nanowell_data(_write_nanowell_dataset(tmp_path, counts=COUNTS))
    return site_metrics(data)


# ── the ranking ──────────────────────────────────────────────────────────────
def test_the_largest_and_smallest_fold_changes_are_picked_in_rank_order_not_address_order(metrics):
    model = ec.extremes(metrics, "A01", "t_d", "count_fc", 2)
    assert _qs(model.largest) == [0, 4] and [r.value for r in model.largest] == [4.0, 3.0]
    assert _qs(model.smallest) == [1, 3] and [r.value for r in model.smallest] == [0.5, 1.0]
    assert [r.rank for r in model.largest + model.smallest] == [1, 2, 1, 2]
    assert model.timepoints == ["t_c", "t_a", "t_d"]
    assert model.largest[0].values == {"t_c": 1.0, "t_a": 1.0, "t_d": 4.0}       # its fold change over time
    assert (model.roster, model.ranked) == (8, 6)
    assert model.excluded == {"seeded later (0 at baseline)": 1, "baseline occluded": 1}


def test_a_well_with_fewer_than_twice_n_shares_them_and_never_shows_one_twice(metrics):
    model = ec.extremes(metrics, "A01", "t_d", "count_fc", 4)
    assert _qs(model.largest) == [0, 4, 2]                         # six ranked: three each
    assert _qs(model.smallest) == [1, 3, 5]                        # the smallest first
    assert "fewer than 8 ranked, so they are split between the two blocks and none is shown twice" in ec.caption(
        model, "Count fold change")
    odd = ec.extremes(metrics[~((metrics["well"] == "A01") & (metrics["nw_q"] == 5))], "A01", "t_d", "count_fc", 4)
    assert (_qs(odd.largest), _qs(odd.smallest)) == ([0, 4, 2], [1, 3])     # five: the odd one to the largest


def test_the_ranking_timepoint_decides_the_picks_and_ties_go_in_address_order(metrics):
    model = ec.extremes(metrics, "A01", "t_a", "count_fc", 2)
    assert _qs(model.largest) == [2, 0]                            # 3×, then the first of the 1× ties
    assert _qs(model.smallest) == [1, 3]


def test_every_metric_is_ranked_by_its_fold_change():
    assert ec.fold_change_metric("n") == ("count_fc", False)
    assert ec.fold_change_metric("brightness") == ("brightness_fc", False)
    assert ec.fold_change_metric("log2_count_fc") == ("log2_count_fc", False)
    assert ec.fold_change_metric("growth_rate") == ("count_fc", True)
    assert nwc.resolve_metric("extremes", "c", None, ["GFP"])[:2] == ("confluence_fc", None)
    assert nwc.resolve_metric("extremes", "brightness", "gfp", ["GFP"])[:2] == ("brightness_fc", "GFP")
    metric, channel, note = nwc.resolve_metric("extremes", "occupancy_class", "gfp", ["GFP"])
    assert (metric, channel) == ("count_fc", None) and "no fold change over time" in note
    metric, _channel, note = nwc.resolve_metric("extremes", "signal", "dapi", ["GFP"])
    assert metric == "count_fc" and "so Count fold change is shown" in note


def test_the_block_size_is_held_to_what_the_view_can_draw():
    assert [ec.clamp_n(v) for v in (0, 3, 99, 2.7, True, "4", float("nan"))] == [1, 3, 12, 2, 5, 5, 5]
    st = NanowellState()
    for saved, kept in ((7, 7), (0, 5), (13, 5), (True, 5), ("6", 5)):
        st.apply_config({"extremes_n": saved})
        assert st.extremes_n == kept
    st.extremes_n = 9
    restored = NanowellState()
    restored.apply_config(st.to_config())
    assert restored.extremes_n == 9


# ── one pass over the input archive ──────────────────────────────────────────
def test_many_nanowells_are_cut_as_one_at_a_time_cuts_them_reading_each_owner_tile_once(tmp_path, monkeypatch):
    import tifffile

    from tests.test_nanowell_page_views import _crop_data, _tif

    tile = np.arange(120 * 260, dtype=np.uint16).reshape(120, 260)
    data, metrics = _crop_data(tmp_path, members=[("exp_phase_A01_F0_t0.tif", _tif(tile))])
    second = metrics.assign(nw_q=1, well_x_um=60.0)                # a second nanowell on the same owner tile
    metrics = metrics._append(second, ignore_index=True)
    one_by_one = {site: ic.site_crops(data, metrics, site) for site in (("A01", 0, 0), ("A01", 1, 0))}
    reads = []
    real = tifffile.imread
    monkeypatch.setattr(tifffile, "imread", lambda *a, **k: reads.append(1) or real(*a, **k))
    together = ic.crops_for_sites(data, metrics, [("A01", 0, 0), ("A01", 1, 0)])
    assert len(reads) == 1                                         # one decode for both nanowells
    assert set(together) == set(one_by_one)
    for site, crops in one_by_one.items():
        assert list(together[site]) == list(crops) == ["t0"]
        assert together[site]["t0"].source == crops["t0"].source == "tile"
        assert np.array_equal(together[site]["t0"].image, crops["t0"].image)
    assert not np.array_equal(together[("A01", 0, 0)]["t0"].image, together[("A01", 1, 0)]["t0"].image)


# ── the page ─────────────────────────────────────────────────────────────────
@pytest.fixture
def page(qtbot, tmp_path, monkeypatch):
    """The page on COUNTS with A01 and B01 selected, and crops that differ per nanowell in brightness: nanowell
    q is a constant image of value 10·(q + 1), with a dark pixel so no crop is flat."""
    def crops_for_sites(_data, _metrics, sites, **_kw):
        out = {}
        for well, q, r in sites:
            image = np.full((12, 12), 10.0 * (q + 1))
            image[0, 0] = 0.0
            out[(well, q, r)] = {tp: Crop(image, "tile", "F0, full resolution") for tp in ("t_c", "t_a", "t_d")}
        return out

    monkeypatch.setattr(nwc, "crops_for_sites", crops_for_sites)
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path, counts=COUNTS))
    ctx.state.selection.selected_wells = {"A01", "B01"}
    return ctx


def _choose_extremes(qtbot, ctx):
    from review.features.nanowell.tab_view import VIEWS

    _loaded(qtbot, ctx)
    seg = ctx.widgets.get("_nw_view_seg")
    seg.setCurrentIndex([k for k, _l in VIEWS].index("extremes"))   # emits currentChanged, as a click does
    qtbot.waitUntil(lambda: bool(_images(ctx)) and not any(t == "Cutting…" for t in _texts(ctx, "nw-extremes-missing")),
                    timeout=10000)


def _fig(ctx):
    return ctx.widgets.get("_nw_fig")


def _texts(ctx, gid):
    fig = _fig(ctx)
    found = list(fig.texts) + [t for ax in fig.axes for t in ax.texts]
    found += [label for ax in fig.axes for label in (ax.title, ax.xaxis.label, ax.yaxis.label)]
    return [t.get_text() for t in found if t.get_gid() == gid and t.get_text()]


def _images(ctx):
    return [im for ax in _fig(ctx).axes for im in ax.get_images() if im.get_gid() == "nw-extremes-image"]


def _row_labels(ctx):
    return _texts(ctx, "nw-extremes-row")


def test_the_view_draws_the_ranked_rows_at_the_latest_timepoint_with_their_crops_over_time(page, qtbot):
    _choose_extremes(qtbot, page)
    assert page.widgets.get("_nw_tp_cb").currentData() == "t_d"          # the well's latest
    assert page.widgets.get("_nw_tp_cb").currentText() == "10 h"         # named by its hours, as on Plotting
    # Six ranked for the default five a block: three each, the largest first and the smallest first.
    assert _row_labels(page) == ["#1  q 0, r 0\n4×", "#2  q 4, r 0\n3×", "#3  q 2, r 0\n2×",
                                 "#1  q 1, r 0\n0.5×", "#2  q 3, r 0\n1×", "#3  q 5, r 0\n1.5×"]
    assert _texts(page, "nw-extremes-block") == ["Largest 3 · Count fold change at t_d",
                                                 "Smallest 3 · Count fold change at t_d"]
    assert _texts(page, "nw-extremes-tp")[:3] == ["t_c", "t_a", "t_d · ranked"]
    assert len(_images(page)) == 6 * 3                                       # a crop per row and timepoint
    # Each crop carries the fold change at its own timepoint: the top row's is 1×, 1×, 4×.
    assert _texts(page, "nw-extremes-value")[:3] == ["1×", "1×", "4×"]
    caption = _texts(page, "nw-extremes-caption")[0]
    # The fixture's wells hold fewer nanowells than rim focus needs to judge one, and the caption says so first.
    unjudged = re.match(r"\d+ in a well with under 10 rims at that timepoint were kept unchecked\. ", caption)
    assert unjudged
    caption = caption[unjudged.end():]
    assert caption.startswith("6 of 8 nanowells in A01 have a count fold change at t_d; not ranked: "
                              "1 baseline occluded, 1 seeded later (0 at baseline)")
    assert "split between the two blocks" in caption


def test_every_crop_is_drawn_on_one_brightness_scale(page, qtbot):
    _choose_extremes(qtbot, page)
    shown = [10.0 * (q + 1) for q in (0, 4, 2, 1, 3, 5) for _tp in range(3)]
    pixels = np.concatenate([np.r_[np.full(143, v), 0.0] for v in shown])
    lo, hi = np.percentile(pixels, [1, 99])
    assert {tuple(np.round(im.get_clim(), 6)) for im in _images(page)} == {tuple(np.round((lo, hi), 6))}


def test_the_spin_box_and_the_timepoint_combo_change_what_is_ranked(page, qtbot):
    _choose_extremes(qtbot, page)
    page.widgets.get("_nw_extremes_spin").setValue(2)
    assert page.state.feature_slice("nanowell").extremes_n == 2
    assert [label.split("\n")[0] for label in _row_labels(page)] == ["#1  q 0, r 0", "#2  q 4, r 0",
                                                                    "#1  q 1, r 0", "#2  q 3, r 0"]
    tp_cb = page.widgets.get("_nw_tp_cb")
    tp_cb.setCurrentIndex(tp_cb.findData("t_a"))
    assert [label.split("\n")[0] for label in _row_labels(page)] == ["#1  q 2, r 0", "#2  q 0, r 0",
                                                                    "#1  q 1, r 0", "#2  q 3, r 0"]


def test_choosing_cell_count_ranks_its_fold_change_not_the_count(page, qtbot):
    _choose_extremes(qtbot, page)
    metric_cb = page.widgets.get("_nw_metric_cb")
    metric_cb.setCurrentIndex([metric_cb.itemData(i) for i in range(metric_cb.count())].index("n"))
    # Ranked by n itself, q6 (5 cells, seeded later) would be second; it has no fold change and is not ranked.
    assert [label.split("\n")[0] for label in _row_labels(page)][:2] == ["#1  q 0, r 0", "#2  q 4, r 0"]
    growth = [metric_cb.itemData(i) for i in range(metric_cb.count())].index("growth_rate")
    assert not metric_cb.model().item(growth).isEnabled()                   # no fold change over time


def test_clicking_a_row_selects_its_nanowell_and_opens_its_detail(page, qtbot):
    _choose_extremes(qtbot, page)
    st = page.state.feature_slice("nanowell")
    canvas = page.widgets.get("_nw_canvas")
    target = next(ax for ax, site in st.extreme_axes.items() if site == ("A01", 1, 0))   # the smallest block
    box = target.get_window_extent()
    ratio = canvas.devicePixelRatioF()
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier,
                     QPoint(int(round((box.x0 + box.x1) / 2 / ratio)),
                            int(round(canvas.height() - (box.y0 + box.y1) / 2 / ratio))))
    assert st.selected_sites == [["A01", 1, 0]]
    assert page.widgets.get("_nw_detail_pane").isVisible()


def test_another_well_is_ranked_among_its_own_nanowells(page, qtbot):
    _choose_extremes(qtbot, page)
    well_cb = page.widgets.get("_nw_well_cb")
    well_cb.setCurrentIndex([well_cb.itemText(i) for i in range(well_cb.count())].index("B01"))
    qtbot.waitUntil(lambda: not any(t == "Cutting…" for t in _texts(page, "nw-extremes-missing")), timeout=10000)
    assert _row_labels(page) == ["#1  q 0, r 0\n3×", "#1  q 1, r 0\n1×"]


def test_a_crop_read_that_failed_says_why_in_its_panels(page, qtbot, monkeypatch):
    def broken(_data, _metrics, _sites, **_kw):
        raise OSError("disk gone")

    monkeypatch.setattr(nwc, "crops_for_sites", broken)
    _loaded(qtbot, page)
    nwc.set_view(page, "extremes")
    qtbot.waitUntil(lambda: any("disk gone" in t for t in _texts(page, "nw-extremes-missing")), timeout=10000)
    assert _images(page) == []
