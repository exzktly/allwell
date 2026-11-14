"""Selecting a nanowell, or closing the site detail, moves the selection rings on the Distribution figure and does
not rebuild the figure; the redraws it no longer runs, and the helpers made cheaper beside it, give the same answers.

Reported: closing the site detail with its ✕ on the Distribution view of sixteen wells over several timepoints
took 15 s. Measured on a synthetic plate of sixteen wells, 1,200 nanowells and ten timepoints (192,000 rows): the
close ran the page's whole redraw — the distribution model, every swarm laid out and drawn — about 4 s here, for a
change that only moves one ring. The ring is the only thing on that figure the selection decides
(``renderer.render_distribution`` reads ``selected`` for the rings alone), so the close and a click on a dot now
re-ring the figure as drawn (``controller._move_selection_rings``) and the full redraw runs only when the figure on
screen cannot be trusted to be current.

Driven through the real page in the minimal shell (``tests/test_nanowell_page.py``): real clicks on the canvas and
on the ✕ button, the rings read back from the figure's artists.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell import renderer as nwr  # noqa: E402
from tests.test_nanowell_page import _click_dot, _loaded, page  # noqa: E402,F401


def _rings(ctx) -> list:
    """``(x, y)`` of every selection ring on the page's figure."""
    return sorted((float(c.get_offsets()[0][0]), float(c.get_offsets()[0][1]))
                  for ax in ctx.widgets.get("_nw_fig").axes for c in ax.collections if c.get_gid() == nwr.RING_GID)


def _dots_of(ctx, site) -> list:
    """``(x, y)`` of every dot drawn for *site*, from the hit map the click test checks against the drawing."""
    st = ctx.state.feature_slice("nanowell")
    return sorted((float(x), float(y)) for _ax, xs, ys, sites in st.hits.entries
                  for x, y, s in zip(xs, ys, sites) if tuple(s) == tuple(site))


@pytest.fixture
def counted(monkeypatch):
    """How many times the Distribution figure was rebuilt."""
    calls = []
    real = nwr.render_distribution

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(nwr, "render_distribution", spy)
    monkeypatch.setattr(nwc, "render_distribution", spy, raising=False)
    return calls


def test_closing_the_site_detail_removes_the_rings_without_rebuilding_the_figure(page, qtbot, counted):  # noqa: F811
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    assert _rings(page) == _dots_of(page, ("A01", 1, 0)) and _rings(page)
    before = len(counted)
    close = page.widgets.get("_nw_detail_close_btn")
    qtbot.waitUntil(lambda: close.isVisible(), timeout=5000)
    QTest.mouseClick(close, Qt.LeftButton)
    assert page.state.feature_slice("nanowell").selected_sites == []
    assert _rings(page) == []
    assert not page.widgets.get("_nw_detail_pane").isVisible()
    assert len(counted) == before, "the close rebuilt the whole Distribution figure"


def test_a_click_on_another_dot_moves_the_ring_onto_that_dot_without_rebuilding(page, qtbot, counted):  # noqa: F811
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    before = len(counted)
    _click_dot(qtbot, page, ("B01", 0, 0))
    assert len(counted) == before
    assert _rings(page) == _dots_of(page, ("B01", 0, 0)) and _rings(page)
    texts = [t.get_text() for t in page.widgets.get("_nw_detail_fig").texts]
    assert len(texts) == 1 and texts[0].startswith("B01 · nanowell 0,0"), texts


def test_the_rings_moved_are_the_rings_a_full_redraw_draws(page, qtbot):  # noqa: F811
    _loaded(qtbot, page)
    nwc.redraw(page)                                  # laid out for the canvas as it now stands
    _click_dot(qtbot, page, ("B01", 2, 0))
    moved = _rings(page)
    nwc.redraw(page)
    assert moved == _rings(page) and moved


def test_another_view_drawn_since_falls_back_to_the_full_redraw(page, qtbot, counted):  # noqa: F811
    """The Trajectories view cleared the figure: the hit map's axes are gone from it, so nothing can be re-ringed."""
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    st = page.state.feature_slice("nanowell")
    hits = st.hits
    nwc.set_view(page, "trajectories")
    st.view, st.hits = "distribution", hits           # as if the figure on screen were still the Distribution's
    before = len(counted)
    nwc.clear_selection(page)
    assert len(counted) == before + 1


def test_stale_metrics_fall_back_to_the_full_redraw(page, qtbot, monkeypatch):  # noqa: F811
    """A baseline changed without a load: :func:`controller.redraw` fetches before it draws, so the selection must
    go through it rather than ring a figure drawn for the old baseline."""
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    st = page.state.feature_slice("nanowell")
    redraws = []
    monkeypatch.setattr(nwc, "redraw", lambda ctx: redraws.append(1))
    st.baseline = "latest" if st.baseline != "latest" else "earliest"
    nwc.clear_selection(page)
    assert redraws == [1]


def test_the_page_s_wells_follow_a_replaced_site_table():
    from review.features.nanowell.collect import NanowellData

    data = NanowellData(sites=pd.DataFrame({"well": ["B01", "A01", "B01"]}))
    assert data.wells == ["A01", "B01"]
    data.wells.append("Z99")                          # a caller's edit does not reach the kept list
    assert data.wells == ["A01", "B01"]
    data.sites = pd.DataFrame({"well": ["C03"]})
    assert data.wells == ["C03"]
    data.sites = pd.DataFrame({"well": []})
    assert data.wells == []


def test_the_rim_focus_tally_over_some_wells_counts_only_those_wells():
    from review.domain.nanowell_rim_focus import tally

    metrics = pd.DataFrame({
        "well": ["A01", "A01", "A01", "B01", "B01", "B01"],
        "nw_q": [0, 0, 1, 0, 1, 2], "nw_r": [0, 0, 0, 0, 0, 0],
        "rim_focus_ok": [False, False, True, False, True, True],
        "recorded_state": ["measured"] * 6,
        "focus": [0.2, 0.3, np.nan, 0.1, 0.9, 0.8],
        "rim_focus_ref": [1.0, 1.0, 1.0, np.nan, 1.0, np.nan],
    })
    a = tally(metrics, ["A01"])
    assert (a.flagged, a.nanowells, a.unjudged, a.unreferenced, a.recorded) == (2, 1, 1, 0, True)
    b = tally(metrics, ["B01"])
    assert (b.flagged, b.nanowells, b.unjudged, b.unreferenced, b.recorded) == (1, 1, 0, 2, True)
    both = tally(metrics)
    assert (both.flagged, both.nanowells, both.unjudged, both.unreferenced) == (3, 2, 1, 2)
    assert tally(metrics, ["C01"]).flagged == 0
    assert not tally(metrics.assign(focus=np.nan), ["A01"]).recorded
