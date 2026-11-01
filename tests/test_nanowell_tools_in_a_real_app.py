"""The Nanowells page's tools where the page meets the rest of the app, each driven in a real ``ReviewApp``.

The page is tested in depth in the fast lane through its real builder, ``build_nanowell_tab``, inside the minimal shell
(``tests/_minimal_shell.py``). That shell has no mode band, no rail page stack and no Home editors, and the fast tests
stub or skip each of them: the view strip's ``register_page_tabs`` call returns early with no band to mount on, the
Anchor queue walks a ``_Rail`` stand-in for the rail's page stack, and the rim focus switch's twin on Cell Gating is
built by hand. Each test here drives one of those seams in a real ``ReviewApp``, and nothing the fast lane already
drives through the real builder.

Full ``ReviewApp`` constructions, so the slow lane (``conftest.py`` marks ``viewer_app`` users ``slow``).
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PySide6")


def _settle(qtbot, ctx, st):
    """Wait out the page's recompute and the redraw matplotlib queues after it, so the test does not end with a draw
    pending on a canvas the teardown is about to delete (as ``test_nanowell_page`` does after each recompute)."""
    qtbot.waitUntil(lambda: not st.computing, timeout=30000)
    timer, canvas = ctx.widgets.get_or_none("_nw_refit_timer"), ctx.widgets.get_or_none("_nw_canvas")
    qtbot.waitUntil(lambda: not (timer is not None and timer.isActive())
                    and not getattr(canvas, "_draw_pending", False), timeout=5000)


def _open(app, qtbot, wells):
    """Show the Nanowells page through the rail, as a click on its rail entry does, and wait for its data."""
    ctx = app._ctx
    ctx.state.selection.selected_wells = set(wells)
    assert app._notebook.setCurrentByName("Nanowells") is not False
    st = ctx.state.feature_slice("nanowell")
    qtbot.waitUntil(lambda: st.metrics is not None and not st.computing, timeout=30000)
    return ctx, st


def _errors(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


# ── N1: the view strip in the shell's mode band ──────────────────────────────
def test_the_view_strip_sits_in_the_mode_band_and_each_tab_draws_its_view(viewer_app, qtbot, tmp_path, caplog):
    """The strip is mounted in the band (``register_page_tabs``), which the minimal shell does not have, so no fast
    test has ever clicked it where a person does. Every tab, clicked there, writes its view and draws it."""
    from review.features.nanowell.tab_view import VIEWS
    from tests.test_nanowell_page import _write_nanowell_dataset

    caplog.set_level(logging.ERROR)
    viewer_app._load_path(_write_nanowell_dataset(tmp_path))
    ctx, st = _open(viewer_app, qtbot, {"A01", "B01"})
    bar = ctx.widgets.get("_nw_view_bar")
    host = viewer_app._page_tabs_host
    assert viewer_app._page_tabs_bar_by_name.get("Nanowells") is bar, "the strip was never mounted in the band"
    assert host.currentWidget() is viewer_app._page_tabs_by_name["Nanowells"]   # the band shows this page's strip
    assert bar.isVisibleTo(viewer_app)
    assert [bar.tabText(i) for i in range(bar.count())] == [label for _k, label in VIEWS]

    drawn = {}
    for i, (key, _label) in enumerate(VIEWS):
        bar.setCurrentIndex(i)                       # what a click on the tab does: currentChanged → set_view
        _settle(qtbot, ctx, st)
        fig = ctx.widgets.get("_nw_fig")
        drawn[key] = (st.view, bool(fig.axes or fig.texts))
    assert drawn == {key: (key, True) for key, _l in VIEWS}
    assert not _errors(caplog)

    # Another page puts its own strip (or none) in the band; coming back puts this one back, on the view left.
    viewer_app._notebook.setCurrentByName("Home")
    assert host.currentWidget() is not viewer_app._page_tabs_by_name["Nanowells"]
    viewer_app._notebook.setCurrentByName("Nanowells")
    assert host.currentWidget() is viewer_app._page_tabs_by_name["Nanowells"]
    assert bar.tabData(bar.currentIndex()) == st.view == VIEWS[-1][0]
    _settle(qtbot, ctx, st)


# ── N2: the Anchor queue with the real rail and the window's own shortcuts ───
def test_the_anchor_queue_walks_with_the_real_rail_and_its_keys_reach_it_in_the_app_window(
        viewer_app, qtbot, tmp_path):
    """The fast walk replaces the rail's page stack with ``_Rail`` and presses the keys on a bare window. Here the
    queue moves the real rail plate's selection, and ←/→/Esc are pressed in the app's window, which carries its own
    shortcuts, with focus on the rail plate where the person last clicked a well."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from review.features.nanowell import controller as nwc
    from tests.test_nanowell_anchor_queue import AT, _dataset_with_unaddressed
    from tests.test_nanowell_full_montage import _widget_point
    from util.nanowell_anchors import anchors_path, read_anchors

    out = _dataset_with_unaddressed(tmp_path)
    viewer_app._load_path(out)
    viewer_app.resize(1500, 900)
    viewer_app.show()
    qtbot.waitExposed(viewer_app)
    ctx, st = _open(viewer_app, qtbot, {"A02"})
    nwc.set_view(ctx, "qc")                                          # where the walk starts
    qtbot.waitUntil(lambda: st.qc_records is not None and st.qc_reading is None, timeout=30000)
    _settle(qtbot, ctx, st)
    plate = ctx.widgets.get("_sidebar_plate")
    viewer_app.activateWindow()
    qtbot.waitUntil(viewer_app.isActiveWindow, timeout=5000)
    plate.setFocus()
    qtbot.waitUntil(plate.hasFocus, timeout=5000)

    def shown():
        qtbot.waitUntil(lambda: st.anchor_pending is None and not st.computing and st.drawn is not None
                        and st.anchor_mode, timeout=30000)
        return st.anchor_queue.index, nwc.page_well(ctx), nwc.page_timepoint(ctx, nwc.page_well(ctx))

    QTest.keyClick(viewer_app, Qt.Key_Right)                         # no walk yet: the key does nothing
    assert st.anchor_queue is None
    ctx.widgets.get("_nw_queue_btn").click()
    assert shown() == (0, "A01", "t_a")
    assert viewer_app._notebook.currentName() == "Nanowells"
    qtbot.waitUntil(lambda: set(plate.selectedWellIds()) == {"A02", "A01"}, timeout=5000)   # the real rail shows it
    QTest.keyClick(viewer_app.focusWidget() or viewer_app, Qt.Key_Right)
    assert shown() == (1, "B01", "t_c")
    qtbot.waitUntil(lambda: set(plate.selectedWellIds()) == {"A02", "B01"}, timeout=5000)   # A01 not left selected
    QTest.keyClick(viewer_app.focusWidget() or viewer_app, Qt.Key_Left)
    assert shown() == (0, "A01", "t_a")

    canvas, at = _widget_point(ctx, st, *AT)                         # a click on the central square anchors it
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, at)
    qtbot.waitUntil(lambda: st.anchor_queue.index == 1, timeout=10000)   # the anchor is written, then it moves on
    assert shown() == (1, "B01", "t_c")
    QTest.keyClick(viewer_app.focusWidget() or viewer_app, Qt.Key_Escape)
    assert st.anchor_queue is None and not st.anchor_mode
    assert st.view == "qc" and st.qc_highlight == {("A01", "t_a")}  # back where it started, the anchor ringed
    assert set(ctx.state.selection.selected_wells) == {"A02"}
    # The rail repaints from the selection on its own coalescing timer, a moment after the state changes.
    qtbot.waitUntil(lambda: set(plate.selectedWellIds()) == {"A02"}, timeout=5000)
    assert sorted(read_anchors(anchors_path(out.parent / "in"))) == [("A01", "t_a")]
    _settle(qtbot, ctx, st)


# ── N3: the rim focus switch and its twin on Home's Cell Gating ──────────────
def test_the_band_s_rim_focus_switch_and_cell_gating_s_are_one_setting_and_line_graphs_counts_follow(
        viewer_app, qtbot, tmp_path):
    """Two controls write one QC parameter: the band's *Rim focus* box and Cell Gating's nanowell focus switch. In the
    fast lane each is built alone; here both exist, so turning one must turn the other, and another tab's own count
    — the Line Graphs status line, which counts the cells ``df_included_mask`` keeps — must move with them."""
    from review.features.lineplot.controller import line_status_text
    from tests.test_nanowell_focus_filter import _dataset

    viewer_app._load_path(_dataset(tmp_path))       # A01: 26 cells, one in a nanowell whose rim was soft at t_a
    ctx, st = _open(viewer_app, qtbot, {"A01"})
    box = ctx.widgets.get("_nw_rim_focus_box")
    gate = ctx.widgets.get("_qc_gate_widgets")["exclude_soft_nanowells"]

    def counted():
        return line_status_text(ctx, [], set(), [], ["A01"])

    assert box.isChecked() and gate.isChecked()
    assert counted() == "1 well(s)  |  25 of 26 nuclei included"

    box.setChecked(False)                                            # on the Nanowells page's band
    qtbot.waitUntil(lambda: st.focus_for == (False,) and not st.computing, timeout=30000)
    assert not gate.isChecked()                                      # Cell Gating's switch shows the same setting
    assert counted() == "1 well(s)  |  26 of 26 nuclei included"

    # And the other way, where a person does it: on Home's Cell Gating, then back to the Nanowells page. (In Split
    # View both are on screen at once; the last test in this file drives that case.)
    viewer_app._notebook.setCurrentByName("Home")
    gate.setChecked(True)
    assert ctx.state.qc.params.exclude_soft_nanowells
    viewer_app._notebook.setCurrentByName("Nanowells")
    qtbot.waitUntil(lambda: box.isChecked() and st.focus_for == (True, 0.75) and not st.computing, timeout=30000)
    assert counted() == "1 well(s)  |  25 of 26 nuclei included"
    _settle(qtbot, ctx, st)


# ── N4: the Statistics view's choices and the Subset through Saved Plots ─────
def test_a_saved_plot_restores_the_statistics_choices_and_the_subset(viewer_app, qtbot, tmp_path):
    """Statistics and the Subset arrived after ``test_nanowell_page_app``'s round trips were written, so no Saved Plot
    test has carried them. Saved from the real card into the real store, changed, and restored through it."""
    from review.features.nanowell.subset_view import preset_of
    from review.features.saved_plots.controller import get_store, restore_saved_plot, save_plot_from_card
    from tests.test_nanowell_page import _write_nanowell_dataset
    from tests.test_nanowell_statistics import PLATE

    viewer_app._load_path(_write_nanowell_dataset(tmp_path, counts=PLATE))
    ctx, st = _open(viewer_app, qtbot, {"A01", "A02", "B01"})
    bar = ctx.widgets.get("_nw_view_bar")
    bar.setCurrentIndex([bar.tabData(i) for i in range(bar.count())].index("statistics"))
    _settle(qtbot, ctx, st)
    for name, data in (("_nw_stats_test_cb", "mannwhitney"), ("_nw_control_cb", "A02")):
        combo = ctx.widgets.get(name)
        combo.setCurrentIndex(combo.findData(data))
    subset = ctx.widgets.get("_nw_subset_cb")
    subset.setCurrentIndex(subset.findData("occupied"))
    _settle(qtbot, ctx, st)
    chosen = (st.view, st.stats_test, st.control, preset_of(st.subset))
    assert chosen == ("statistics", "mannwhitney", "A02", "occupied")

    assert save_plot_from_card(ctx, "Nanowells", b"\x89PNG", "Nanowells").startswith("Saved")
    viewer_app._notebook.setCurrentByName("Home")
    bar.setCurrentIndex(0)
    st.stats_test, st.control, st.subset = "welch", "", type(st.subset)()
    restore_saved_plot(ctx, get_store(ctx).records[-1].id)
    _settle(qtbot, ctx, st)
    restored = (st.view, st.stats_test, st.control, preset_of(st.subset))
    assert restored == chosen
    assert viewer_app._notebook.currentName() == "Nanowells"
    assert bar.tabData(bar.currentIndex()) == "statistics"           # the band's strip follows the restore
    assert ctx.widgets.get("_nw_stats_test_cb").currentData() == "mannwhitney"


# ── N5: the crops pop-out, owned by the app's window ─────────────────────────
def test_the_crops_pop_out_is_owned_by_the_app_window_and_go_to_nanowell_moves_the_band(viewer_app, qtbot, tmp_path):
    """The pop-out is a non-modal dialog parented to ``ctx.window`` so it stays above the app (on Windows a plain
    window dropped behind it). In the minimal shell that parent is a bare ``QMainWindow``; here it is the app. Its
    *Go to nanowell* switches the page to Identity, and the band's strip must say so."""
    from review.features.nanowell import controller as nwc
    from tests.test_nanowell_page import _write_nanowell_dataset

    viewer_app._load_path(_write_nanowell_dataset(tmp_path))
    viewer_app.show()
    qtbot.waitExposed(viewer_app)
    ctx, st = _open(viewer_app, qtbot, {"A01", "B01"})
    st.selected_sites = [["A01", 1, 0]]
    nwc.redraw(ctx)
    ctx.widgets.get("_nw_popout_btn").click()
    popout = ctx.widgets.get("_nw_popout")
    qtbot.waitUntil(popout.isVisible, timeout=5000)
    assert popout.parent() is viewer_app and popout.window() is popout and not popout.isModal()
    fig = ctx.widgets.get("_nw_popout_fig")
    qtbot.waitUntil(lambda: bool(fig.axes), timeout=10000)
    assert fig.texts[0].get_text().startswith("A01 · q 1, r 0 over time") and not [t for t in fig.texts if t.get_gid() == "nw-popout-empty"]   # the crops, not the hint

    ctx.widgets.get("_nw_popout_go_to_btn").click()
    _settle(qtbot, ctx, st)
    bar = ctx.widgets.get("_nw_view_bar")
    assert (st.view, st.well, st.identity_mode) == ("identity", "A01", "blink")
    assert bar.tabData(bar.currentIndex()) == "identity"
    assert popout.isVisible()                                        # the pop-out stays open beside it
    popout.close()


# ── N6: a Cell Gating change reaches the page while it is on screen ──────────
def test_in_split_view_a_cell_gating_focus_change_reaches_the_nanowells_page_beside_it(viewer_app, qtbot, tmp_path):
    """Split View puts Home's Cell Gating and the Nanowells page on screen together. A change to Cell Gating's
    nanowell focus switch must move the band's *Rim focus* box and recompute the page's metrics for the new rule
    then, not when the page is next shown: the QC layer's after-apply fan-out redrew Line, Distribution and Heat Map
    only, so the page went on drawing the soft nanowell the rule now leaves out, with the box beside it still ticked."""
    from review.core import split_view as sv
    from tests.test_nanowell_focus_filter import _dataset

    viewer_app._load_path(_dataset(tmp_path))
    viewer_app.show()
    qtbot.waitExposed(viewer_app)
    ctx, st = _open(viewer_app, qtbot, {"A01"})
    sv.enter_split(ctx, "Home", "Nanowells")
    assert ctx.runtime.split.active
    box = ctx.widgets.get("_nw_rim_focus_box")
    gate = ctx.widgets.get("_qc_gate_widgets")["exclude_soft_nanowells"]
    assert ctx.widgets.get("_nw_page_visible")() and box.isChecked() and gate.isChecked()
    assert st.focus_for == (True, 0.75)

    gate.setChecked(False)                                           # on Cell Gating, in the other pane
    assert not ctx.state.qc.params.exclude_soft_nanowells
    try:
        qtbot.waitUntil(lambda: st.focus_for == (False,) and not st.computing and not box.isChecked(), timeout=20000)
    except qtbot.TimeoutError:
        pass                                                         # the assertion below names what was left stale
    # The metrics are computed for the rule in force, and the band says what is in force.
    assert (st.focus_for, box.isChecked()) == ((False,), False), "the Nanowells page kept the previous focus rule"
    _settle(qtbot, ctx, st)
    sv.exit_split(ctx)
