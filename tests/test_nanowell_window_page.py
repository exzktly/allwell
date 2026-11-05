"""Drawing a Distribution window on the real page: the drag, the band and rings, a click still selecting.

Driven through the real page in the minimal shell (``tests/test_nanowell_page.py``): the press, the moves and the
release are real mouse events on the canvas, and what is checked is read back from the figure's artists and the
page's state. The page draws wells A01 and B01 (metric *Cell count*, timepoints ``t_c`` and ``t_a``); the cell
counts in ``COUNTS`` are the values, so the members of a window are worked out from them by hand below.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from review.features.nanowell import _window_controller as wnc  # noqa: E402
from tests.test_nanowell_page import COUNTS, _click_dot, _loaded, page  # noqa: E402,F401


def _drag(ctx, ax, data_from, data_to):
    """Press at *data_from*, move, release at *data_to* (data units of *ax*), as a person drags."""
    canvas = ctx.widgets.get("_nw_canvas")
    ratio = canvas.devicePixelRatioF()

    def at(xy):
        px, py = ax.transData.transform(xy)
        return QPoint(int(round(px / ratio)), int(round(canvas.height() - py / ratio)))

    middle = ((data_from[0] + data_to[0]) / 2, (data_from[1] + data_to[1]) / 2)
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, at(data_from))
    QTest.mouseMove(canvas, at(middle))
    QTest.mouseMove(canvas, at(data_to))
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, at(data_to))


def _first_panel(ctx):
    st = ctx.state.feature_slice("nanowell")
    return st.hits.panels[0][0]


def _counts(sites):
    """``(well, cells at t_c, cells at t_a)`` for each site, read from the dataset's own table of counts."""
    return {(w, COUNTS[w]["t_c"][q], COUNTS[w]["t_a"][q]) for w, q, _r in sites}


def _bands(ctx):
    """``{panel axes: its window bands}`` — the panels the hit map names, not the figure's key axes."""
    st = ctx.state.feature_slice("nanowell")
    return {id(ax): [p for p in ax.patches if p.get_gid() == "nw-window-band"] for ax, _index in st.hits.panels}


def test_a_drag_draws_a_window_whose_members_are_the_model_s(page, qtbot):
    st = _loaded(qtbot, page)
    _drag(page, _first_panel(page), (-0.4, 1.5), (1.4, 4.5))
    assert len(st.windows) == 1
    window = next(iter(st.windows.values()))
    assert set(window.keys) == {"t_c", "t_a"} and window.rule == "every"
    assert window.lo == pytest.approx(1.5, abs=0.05) and window.hi == pytest.approx(4.5, abs=0.05)
    _window, result = wnc.current(st)
    # Of A01's and B01's nanowells, only these have 2-4 cells at both timepoints.
    assert _counts(result.sites()) == {("A01", 2, 4), ("B01", 3, 3)}
    bands = _bands(page)
    assert len(bands) == 2 and all(len(patches) == 1 for patches in bands.values())
    rings = [c for ax in page.widgets.get("_nw_fig").axes for c in ax.collections if c.get_gid() == "nw-window-ring"]
    assert rings


def test_a_click_still_selects_one_nanowell(page, qtbot):
    st = _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    assert st.selected_sites == [["A01", 1, 0]]
    assert st.windows == {}


def test_the_clear_button_clears(page, qtbot):
    st = _loaded(qtbot, page)
    _drag(page, _first_panel(page), (-0.4, 1.5), (1.4, 4.5))
    assert len(st.windows) == 1
    wnc.clear_window(page)
    assert st.windows == {}
    assert all(not patches for patches in _bands(page).values())
