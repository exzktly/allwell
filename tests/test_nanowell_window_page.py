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


def test_one_window_gate_at_a_time_a_second_box_selects_from_every_nanowell(page, qtbot, monkeypatch):
    """The Subset holds one window gate. The first window keeps A01 (1,0) and B01 (0,0); a second box over t_a at 1.5-2.5
    cells holds neither of them (4 and 3 cells there), so stacked on the first it would hold nothing — drawn against
    every nanowell it holds A01 (0,0) and B01 (3,0), the two with 2 cells at t_a."""
    st = _drawn(page, qtbot)
    qtbot.mouseClick(page.widgets.get("_nw_window_subset_btn"), Qt.LeftButton)
    assert _drawn_sites(st) == {("A01", 1, 0), ("B01", 0, 0)}
    said = []
    monkeypatch.setattr(page.notify, "set_status", said.append)
    _drag(page, _first_panel(page), (0.6, 1.5), (1.4, 2.5))                 # only the t_a column, 1.5-2.5 cells
    assert _subset_windows(st) == [] and said[-1].startswith("The earlier window was taken out of the Subset")
    assert set(wnc.current(st)[1].sites()) == {("A01", 0, 0), ("B01", 3, 0)}
    assert ("A01", 0, 0) in _drawn_sites(st) and ("A01", 2, 0) in _drawn_sites(st)       # every nanowell back
    window = next(iter(st.windows.values()))
    assert window.n_at_drawing == 2 and wnc.strip_text(st).find("changed since") == -1
    qtbot.mouseClick(page.widgets.get("_nw_window_subset_btn"), Qt.LeftButton)
    (only,) = _subset_windows(st)
    assert only.values == ("Window 1",) and set(only.sites) == {("A01", 0, 0), ("B01", 3, 0)}
    assert _drawn_sites(st) == {("A01", 0, 0), ("B01", 3, 0)}


def test_use_as_subset_replaces_a_window_gate_already_in_the_subset_and_keeps_the_rest(page, qtbot, monkeypatch):
    from review.features.nanowell import controller as nwc
    from review.features.nanowell import subset_compute as ss

    st = _drawn(page, qtbot)
    members = set(wnc.current(st)[1].sites())
    other = ss.Condition("n", "ge", 0, at="latest")
    stale = ss.Condition(ss.WINDOW, "in", values=("Window 1",), sites=(("A01", 3, 0),))
    assert nwc.set_subset(page, ss.Subset((other, stale), "all"))           # the window stays drawn over it
    said = []
    monkeypatch.setattr(page.notify, "set_status", said.append)
    qtbot.mouseClick(page.widgets.get("_nw_window_subset_btn"), Qt.LeftButton)
    kept_other, (only,) = st.subset.conditions[0], _subset_windows(st)
    assert kept_other == other and len(st.subset.conditions) == 2
    assert set(only.sites) == members and said[-1].endswith("It replaces the earlier window.")


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


def test_a_short_figure_with_a_long_footer_still_draws():
    """Reported: after Use as Subset and an ordinary condition, the page raised ``bottom cannot be >= top`` — on a
    short figure, every line the footer owed asked for more height than there was. The page's footer is in its
    Details field now; a figure that writes its own (Batch Export) keeps the panels a share of the figure and every
    line."""
    from matplotlib.figure import Figure

    from review.features.nanowell import distribution_compute as dc
    from review.features.nanowell.renderer import render_distribution
    model = dc.DistributionModel("n", "n", "Cell count", "time", True, [], alerts=[f"Alert {i}." for i in range(8)])
    fig = Figure(figsize=(8.0, 2.0))
    render_distribution(fig, model)                                      # raised ValueError before
    assert fig.subplotpars.top - fig.subplotpars.bottom >= 0.12 - 1e-9
    written = [t.get_text() for t in fig.texts if t.get_gid() == "nw-footer"]
    assert all(f"Alert {i}." in written for i in range(8))


def test_the_distribution_s_footer_is_in_its_details_field_not_the_figure(page, qtbot):
    """Asked for: every line under the Distribution in a collapsible field, so the plot has the room. Collapsed, the
    field still says the counts and how many alerts and notes it holds, so what the figure leaves out is never out of
    sight; a figure that writes its own footer (Batch Export, ``footer_out`` off) still writes every line."""
    from matplotlib.figure import Figure

    from review.features.nanowell import controller as nwc
    from review.features.nanowell import subset_compute as ss
    from review.features.nanowell.renderer import render_distribution

    st = _loaded(qtbot, page)
    assert nwc.set_subset(page, ss.Subset((ss.Condition("n0", "ge", 1),), "all"))
    fig = page.widgets.get("_nw_fig")
    assert not [t for t in fig.texts if (t.get_gid() or "").startswith("nw-footer")]       # none in the figure
    assert fig.subplotpars.bottom == pytest.approx(0.10)                                    # the panels have the room
    footer = st.hits.footer
    assert footer.alerts and footer.notes
    assert any(a.startswith("Starting cells ≥1") for a in footer.alerts)
    summary, body = page.widgets.get("_nw_details_summary"), page.widgets.get("_nw_details_body")
    button = page.widgets.get("_nw_details_btn")
    assert summary.isVisibleTo(page.widgets.get("_nw_card").parentWidget().parentWidget()) or not summary.isHidden()
    n_alerts = len(footer.alerts) + len(footer.warnings)
    assert summary.text() == (f"{footer.counts} · ⚠ {n_alerts} alert{'s' if n_alerts != 1 else ''} · "
                              f"{len(footer.notes)} notes")
    assert summary.objectName() == "Warn" and body.isHidden() and not button.isChecked()
    button.click()                                                                          # open it
    lines = body.text().split("\n")
    assert not body.isHidden()
    assert lines == [f"⚠ {a}" for a in [*footer.warnings, *footer.alerts]] + [f"• {n}" for n in footer.notes]
    nwc.set_view(page, "array")
    assert summary.parentWidget().isHidden()                                                 # Distribution only
    own = Figure(figsize=(8, 5))
    render_distribution(own, st.model)
    written = " ".join(t.get_text() for t in own.texts)
    assert footer.counts in written and all(a[:30] in written for a in footer.alerts)
