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


def test_clearing_removes_the_band(page, qtbot):
    st = _loaded(qtbot, page)
    _drag(page, _first_panel(page), (-0.4, 1.5), (1.4, 4.5))
    assert len(st.windows) == 1
    wnc.clear_window(page)
    assert st.windows == {}
    assert all(not patches for patches in _bands(page).values())


# ── the strip ────────────────────────────────────────────────────────────────
def _drawn(page, qtbot):
    st = _loaded(qtbot, page)
    _drag(page, _first_panel(page), (-0.4, 1.5), (1.4, 4.5))
    return st


def _members(st):
    return _counts(wnc.current(st)[1].sites())


def _type(page, lo, hi):
    for name, text in (("_nw_window_lo", lo), ("_nw_window_hi", hi)):
        page.widgets.get(name).setText(text)
    page.widgets.get("_nw_window_hi").editingFinished.emit()


def test_the_strip_states_the_counts(page, qtbot):
    _drawn(page, qtbot)
    label = page.widgets.get("_nw_window_label")
    assert page.widgets.get("_nw_window_label").parent().isVisible()
    assert label.text().startswith("Window ") and "2 inside at every covered timepoint · 5 at any" in label.text()


def test_the_clear_button_clears_from_the_strip(page, qtbot):
    st = _drawn(page, qtbot)
    qtbot.mouseClick(page.widgets.get("_nw_window_clear_btn"), Qt.LeftButton)
    assert st.windows == {} and not page.widgets.get("_nw_window_label").parent().isVisible()


def test_typed_bounds_move_the_window(page, qtbot):
    st = _drawn(page, qtbot)
    _type(page, "2", "3")
    window = next(iter(st.windows.values()))
    assert (window.lo, window.hi) == (2.0, 3.0)
    assert _members(st) == {("B01", 3, 3)}


def test_typed_bounds_the_wrong_way_round_are_swapped(page, qtbot):
    st = _drawn(page, qtbot)
    _type(page, "3", "2")
    window = next(iter(st.windows.values()))
    assert (window.lo, window.hi) == (2.0, 3.0)


def test_text_that_is_not_a_number_is_put_back(page, qtbot):
    st = _drawn(page, qtbot)
    before = next(iter(st.windows.values()))
    _type(page, "abc", "3")
    assert next(iter(st.windows.values())) == before
    assert page.widgets.get("_nw_window_lo").text() == f"{before.lo:.6g}"


def test_any_widens_the_window(page, qtbot):
    st = _drawn(page, qtbot)
    page.widgets.get("_nw_window_rule_cb").setCurrentIndex(1)
    assert next(iter(st.windows.values())).rule == "any"
    assert _members(st) == {("A01", 1, 2), ("A01", 2, 4), ("A01", 0, 3), ("B01", 3, 3), ("B01", 1, 2)}


def test_another_metric_hides_the_window_and_the_first_brings_it_back(page, qtbot):
    from review.features.nanowell import controller as nwc

    st = _drawn(page, qtbot)
    strip = page.widgets.get("_nw_window_label").parent()
    nwc.set_metric(page, "c")
    qtbot.waitUntil(lambda: st.window_model is not None and st.window_model.column == "c", timeout=5000)
    assert not strip.isVisible() and all(not patches for patches in _bands(page).values())
    nwc.set_metric(page, "n")
    qtbot.waitUntil(lambda: st.window_model is not None and st.window_model.column == "n", timeout=5000)
    assert strip.isVisible() and all(len(patches) == 1 for patches in _bands(page).values())


# ── Use as Subset ────────────────────────────────────────────────────────────
def _drawn_sites(st):
    """Every nanowell the Distribution figure's model holds, drawn or not."""
    return {site for swarm in st.window_model.swarms() for site in swarm.sites}


def _subset_windows(st):
    from review.features.nanowell import subset_compute as ss

    return [c for c in st.subset.conditions if ss.is_window(c.field)]


def test_use_as_subset_filters_the_distribution_itself(page, qtbot):
    st = _drawn(page, qtbot)
    members = set(wnc.current(st)[1].sites())
    assert members == {("A01", 1, 0), ("B01", 0, 0)} and len(_drawn_sites(st)) > 2
    qtbot.mouseClick(page.widgets.get("_nw_window_subset_btn"), Qt.LeftButton)
    (condition,) = _subset_windows(st)
    assert (condition.values, condition.op, set(condition.sites)) == (("Window 1",), "in", members)
    assert st.windows == {} and _drawn_sites(st) == members
    assert not page.widgets.get("_nw_window_label").parent().isVisible()


def test_a_second_window_stacks_and_narrows_further(page, qtbot):
    st = _drawn(page, qtbot)
    qtbot.mouseClick(page.widgets.get("_nw_window_subset_btn"), Qt.LeftButton)
    _drag(page, _first_panel(page), (0.6, 3.5), (1.4, 4.5))                 # only the t_a column, 3.5-4.5 cells
    assert set(wnc.current(st)[1].sites()) == {("A01", 1, 0)}
    qtbot.mouseClick(page.widgets.get("_nw_window_subset_btn"), Qt.LeftButton)
    first, second = _subset_windows(st)
    assert (first.values, second.values) == (("Window 1",), ("Window 2",))
    assert set(second.sites) == {("A01", 1, 0)} and _drawn_sites(st) == {("A01", 1, 0)}
    assert st.subset.match == "all"


def test_use_as_subset_refuses_when_the_subset_keeps_nanowells_meeting_any_of_several_conditions(page, qtbot,
                                                                                                    monkeypatch):
    from review.features.nanowell import controller as nwc
    from review.features.nanowell import subset_compute as ss

    st = _loaded(qtbot, page)
    either = ss.Subset((ss.Condition("n", "ge", 0, at="latest"), ss.Condition("n", "le", 9, at="earliest")), "any")
    assert nwc.set_subset(page, either)
    qtbot.waitUntil(lambda: st.window_model is not None and not st.computing, timeout=10000)
    _drag(page, _first_panel(page), (-0.4, 1.5), (1.4, 4.5))
    said = []
    monkeypatch.setattr(page.notify, "set_status", said.append)
    qtbot.mouseClick(page.widgets.get("_nw_window_subset_btn"), Qt.LeftButton)
    assert said[-1].startswith("The subset keeps nanowells that meet any condition, so a window could not narrow it")
    assert st.subset == either and len(st.windows) == 1


def test_the_subset_editor_lists_the_window_and_removing_it_brings_the_others_back(page, qtbot):
    st = _drawn(page, qtbot)
    qtbot.mouseClick(page.widgets.get("_nw_window_subset_btn"), Qt.LeftButton)
    editor = page.widgets.get("_nw_subset_editor")
    (row,) = editor.rows
    assert row.window_label.text() == "“Window 1” · 2 nanowells" and not row.field_cb.isEnabled()
    assert editor.written().conditions[0].sites == (("A01", 1, 0), ("B01", 0, 0))      # the row keeps its nanowells
    editor.remove(row)
    editor.apply()
    assert _subset_windows(st) == [] and len(_drawn_sites(st)) > 2


def test_a_subset_of_one_condition_set_to_any_is_narrowed_under_all(page, qtbot):
    """With one condition *any* and *all* mean the same, so a window may narrow it; the new subset is under *all*, or
    the window would widen the subset instead (a nanowell meeting either condition would be kept)."""
    from review.features.nanowell import controller as nwc
    from review.features.nanowell import subset_compute as ss

    st = _loaded(qtbot, page)
    assert nwc.set_subset(page, ss.Subset((ss.Condition("n", "ge", 0, at="latest"),), "any"))
    qtbot.waitUntil(lambda: st.window_model is not None and not st.computing, timeout=10000)
    _drag(page, _first_panel(page), (-0.4, 1.5), (1.4, 4.5))
    qtbot.mouseClick(page.widgets.get("_nw_window_subset_btn"), Qt.LeftButton)
    assert st.subset.match == "all" and len(st.subset.conditions) == 2
    assert _drawn_sites(st) == {("A01", 1, 0), ("B01", 0, 0)}


# ── controls the strip offers, pressed ───────────────────────────────────────
def test_this_panel_only_restricts_the_window_to_the_panel_it_was_drawn_in(page, qtbot):
    st = _drawn(page, qtbot)
    assert {site[0] for site in wnc.current(st)[1].sites()} == {"A01", "B01"}
    page.widgets.get("_nw_window_panel_box").setChecked(True)
    window = next(iter(st.windows.values()))
    assert window.panel_only is True
    assert {site[0] for site in wnc.current(st)[1].sites()} == {window.panel[:3]} == {"A01"}
    bands = [len(patches) for patches in _bands(page).values()]
    assert sorted(bands) == [0, 1]                                         # only the first panel carries the band


def test_a_setting_changed_after_the_window_was_drawn_is_named_in_the_strip(page, qtbot):
    from review.features.nanowell import controller as nwc

    st = _drawn(page, qtbot)
    label = page.widgets.get("_nw_window_label")
    assert "changed since the window was drawn" not in label.text()
    nwc.set_cohort(page, True)
    qtbot.waitUntil(lambda: st.window_model is not None and not st.computing and "Fixed cohort" in label.text(),
                    timeout=10000)
    assert "Fixed cohort changed since the window was drawn: 2 → " in label.text()
