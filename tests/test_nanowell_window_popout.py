"""The Distribution window in the pop-out: its rows, pages, clicks and controls — and the bounded store of crops it
pages through.

Asked for: the window's nanowells shown in the pop-out as rows of crops over time. A window can hold hundreds of
nanowells and ``st.crops`` was never pruned, so the store keeps a bounded number and never lets go of the nanowell
selected or the page on screen.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell.crop_cache_compute import CropCache  # noqa: E402
from review.features.nanowell.state import NanowellState  # noqa: E402
from tests.test_nanowell_page import _loaded, page  # noqa: E402,F401


def test_the_crop_store_keeps_its_bound():
    cache = CropCache(maxlen=3)
    cache.protected = {"a"}
    cache["a"] = 1
    cache.update({"b": 2, "c": 3, "d": 4, "e": 5})
    assert list(cache) == ["a", "d", "e"]               # the oldest unprotected go; "a" is the oldest and stays


def test_every_place_that_empties_the_store_keeps_it_bounded(page, qtbot):  # noqa: F811
    assert isinstance(NanowellState().crops, CropCache)
    fresh = NanowellState()
    fresh.crops["x"] = 1
    fresh.reset_data()
    assert isinstance(fresh.crops, CropCache) and not fresh.crops
    st = _loaded(qtbot, page)
    nwc.set_image_channel(page, "GFP")
    assert isinstance(st.crops, CropCache)


# ── the pop-out on the real page ─────────────────────────────────────────────
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from review.features.nanowell import _window_controller as wnc  # noqa: E402
from review.features.nanowell.window_compute import DistributionWindow, WindowResult, WindowRow  # noqa: E402
from tests.test_nanowell_window_page import _counts, _drag, _first_panel  # noqa: E402


@pytest.fixture
def spied(monkeypatch):
    """Every call the pop-out makes to the rows renderer: ``[(blocks, crops, kwargs)]``."""
    calls = []
    real = wnc.render_site_rows

    def spy(fig, model, blocks, crops, **kwargs):
        calls.append(([(title, [row.site for row in rows]) for title, rows in blocks], dict(crops), kwargs))
        return real(fig, model, blocks, crops, **kwargs)

    monkeypatch.setattr(wnc, "render_site_rows", spy)
    return calls


def _drawn(page, qtbot):
    st = _loaded(qtbot, page)
    _drag(page, _first_panel(page), (-0.4, 1.5), (1.4, 4.5))
    return st


def _texts(page):
    fig = page.widgets.get("_nw_popout_fig")
    return [t.get_text() for t in fig.texts + [t for ax in fig.axes for t in ax.texts]]


def test_open_in_pop_out_draws_the_window_s_members_as_rows(page, qtbot, spied):  # noqa: F811
    st = _drawn(page, qtbot)
    qtbot.mouseClick(page.widgets.get("_nw_window_popout_btn"), Qt.LeftButton)
    assert page.widgets.get("_nw_popout").isVisible() and st.popout_source == "window"
    wanted = wnc.current(st)[1].sites()
    assert len(wanted) == 2 and _counts(wanted) == {("A01", 2, 4), ("B01", 3, 3)}
    assert spied and all(blocks == [("Rows 1–2 of 2", wanted)] for blocks, _crops, _kw in spied)
    assert all(kw["with_well"] is True for _b, _c, kw in spied)


def test_rows_page_by_twenty_four(page, qtbot, spied, monkeypatch):  # noqa: F811
    st = _loaded(qtbot, page)
    window = DistributionWindow("n", "time", ("t_c", "t_a"), 0, 9)
    rows = [WindowRow(("A01", i, 0), i + 1, float(30 - i), {}) for i in range(30)]
    monkeypatch.setattr(wnc, "current", lambda _st: (window, WindowResult(rows, "t_a", 30, 30)))
    wnc.open_window_popout(page)
    assert [b[0][0] for b, _c, _k in spied[-1:]] == ["Rows 1–24 of 30"]
    assert spied[-1][0][0][1] == [r.site for r in rows[:24]]
    wnc.step_window_page(page, 1)
    assert spied[-1][0][0] == ("Rows 25–30 of 30", [r.site for r in rows[24:]])
    wnc.step_window_page(page, 1)                                          # already on the last page
    assert spied[-1][0][0][0] == "Rows 25–30 of 30" and st.window_page == 1
    wnc.step_window_page(page, -5)
    assert st.window_page == 0


def test_a_click_on_a_row_selects_its_nanowell(page, qtbot, spied):  # noqa: F811
    st = _drawn(page, qtbot)
    wnc.open_window_popout(page)
    qtbot.waitUntil(lambda: bool(st.popout_hits), timeout=10000)
    canvas = page.widgets.get("_nw_popout_canvas")
    ax, site = next(iter(st.popout_hits.items()))
    x, y, w, h = ax.bbox.bounds
    ratio = canvas.devicePixelRatioF()
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier,
                     QPoint(int(round((x + w / 2) / ratio)), int(round(canvas.height() - (y + h / 2) / ratio))))
    assert st.selected_sites == [[*site]]


def test_the_window_mode_survives_a_restore():
    state = NanowellState()
    state.popout_source = "window"
    restored = NanowellState()
    restored.apply_config(state.to_config())
    assert restored.popout_source == "window"


def test_a_pop_out_still_on_the_selected_nanowell_has_nothing_to_click(page, qtbot):  # noqa: F811
    st = _drawn(page, qtbot)
    wnc.open_window_popout(page)
    qtbot.waitUntil(lambda: bool(st.popout_hits), timeout=10000)
    wnc.set_popout_source(page, "selected")
    assert st.popout_hits == {} and any("Select a nanowell" in t for t in _texts(page))


def test_the_pager_buttons_and_the_show_choice_work_on_the_page(page, qtbot, spied, monkeypatch):  # noqa: F811
    st = _loaded(qtbot, page)
    window = DistributionWindow("n", "time", ("t_c", "t_a"), 0, 9)
    rows = [WindowRow(("A01", i, 0), i + 1, float(30 - i), {}) for i in range(30)]
    monkeypatch.setattr(wnc, "current", lambda _st: (window, WindowResult(rows, "t_a", 30, 30)))
    wnc.open_window_popout(page)
    prev, label, following = (page.widgets.get(n) for n in ("_nw_popout_page_prev", "_nw_popout_page_label",
                                                            "_nw_popout_page_next"))
    assert label.text() == "Page 1 of 2" and following.isVisibleTo(page.widgets.get("_nw_popout"))
    qtbot.mouseClick(following, Qt.LeftButton)
    assert st.window_page == 1 and label.text() == "Page 2 of 2" and spied[-1][0][0][0] == "Rows 25–30 of 30"
    qtbot.mouseClick(prev, Qt.LeftButton)
    assert st.window_page == 0 and spied[-1][0][0][0] == "Rows 1–24 of 30"
    source = page.widgets.get("_nw_popout_source_cb")
    assert source.itemText(1) == "Window (30)"
    source.setCurrentIndex(0)                                             # This nanowell
    assert st.popout_source == "selected" and not following.isVisibleTo(page.widgets.get("_nw_popout"))


def test_the_composite_s_first_page_holds_the_largest_values_of_the_whole_window(page, qtbot, monkeypatch):  # noqa: F811
    """A sort by value used to be applied after the page of ranks was cut, so page 1 of a 200-nanowell window held the
    first 91 by rank, sorted among themselves -- the largest values could be on page 3. Through the real pop-out path,
    with the window and the metric's values stood in."""
    st = _loaded(qtbot, page)
    window = DistributionWindow("n", "time", ("t_c", "t_a"), 0, 9)
    rows = [WindowRow(("A01", i, 0), i + 1, 0.0, {}) for i in range(200)]
    values = {row.site: float((i * 37) % 200) for i, row in enumerate(rows)}            # against the rank
    monkeypatch.setattr(wnc, "current", lambda _st: (window, WindowResult(rows, "t_a", 200, 200)))
    monkeypatch.setattr(wnc, "composite_order", lambda _ctx, _st, sites, _tp: (
        {s: values[s] for s in sites}, "Cell count, largest first"))
    monkeypatch.setattr(wnc, "_want_crops", lambda *_a, **_k: None)      # no crops to cut for stand-in nanowells
    placed: list = []
    real = wnc.cc.composite
    monkeypatch.setattr(wnc.cc, "composite", lambda members, **kw: placed.append(list(members)) or real(members, **kw))
    st.popout_layout, st.composite_desc, st.window_page = "hex", True, 0
    wnc.open_window_popout(page)
    qtbot.waitUntil(lambda: bool(placed), timeout=10000)
    largest = sorted(rows, key=lambda r: -values[r.site])[:wnc.cc.COMPOSITE_PAGE]
    assert {r.site for r in placed[-1]} == {r.site for r in largest}
