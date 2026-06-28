"""Split View: NamedPageStack detach/reattach + enter/exit reparenting.

Pins the load-bearing invariants behind the side-by-side Split View:
1. ``NamedPageStack.detachPage`` → ``reattachPage`` round-trips ``pageNames()``
   (and name-keyed lookup) back to the original add order.
2. ``enter_split`` reparents two top-level pages into ``app._split_pane`` and
   hides the notebook; ``exit_split`` puts them back in order. While split,
   ``visible_centre_tabs`` reports both panes and ``current_centre_tab`` the
   focused one.

Qt is required, so the module skips when PySide6 isn't importable. ``offscreen``
keeps it headless. ``on_tab_change`` (called by enter/exit) early-returns unless
the app exposes ``_line_ax_mean``, so a bare stub app exercises the reparenting
without dragging in the whole runtime.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


def _new_stack(names):
    from PySide6.QtWidgets import QApplication, QLabel

    from well_viewer.core.centre_view import NamedPageStack

    QApplication.instance() or QApplication([])
    st = NamedPageStack()
    widgets = {}
    for n in names:
        w = QLabel(n)
        st.addPage(n, w)
        widgets[n] = w
    return st, widgets


def test_detach_reattach_roundtrips_order():
    names = ["A", "B", "C", "D"]
    st, widgets = _new_stack(names)
    assert st.pageNames() == names

    w = st.detachPage("B")
    assert w is widgets["B"]
    assert st.pageNames() == ["A", "C", "D"]
    assert st.setCurrentByName("B") is False  # detached → not selectable

    st.reattachPage("B", w)
    assert st.pageNames() == names            # original slot restored
    assert st.setCurrentByName("B") is True


def test_detach_two_then_reattach_restores_order():
    names = ["A", "B", "C", "D"]
    st, widgets = _new_stack(names)
    wb = st.detachPage("B")
    wd = st.detachPage("D")
    assert st.pageNames() == ["A", "C"]
    st.reattachPage("B", wb)
    st.reattachPage("D", wd)
    assert st.pageNames() == names


def test_detach_unknown_page_returns_none():
    st, _ = _new_stack(["A", "B"])
    assert st.detachPage("ZZ") is None
    assert st.pageNames() == ["A", "B"]


def _split_app():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QSplitter

    st, widgets = _new_stack(["Plotting", "Statistics", "Saved Plots"])

    class _App:
        pass

    app = _App()
    app._notebook = st
    app._split_pane = QSplitter(Qt.Horizontal)
    app._split_pane.hide()
    app._split_active = False
    app._split_tab_a = app._split_tab_b = app._split_focused = None
    app._split_pane_widgets = {}
    return app, st, widgets


def test_enter_split_reparents_and_reports_both_panes():
    from well_viewer.core import tab_controller as tc

    app, st, _ = _split_app()
    tc.enter_split(app, "Plotting", "Saved Plots")

    assert app._split_active is True
    assert app._split_pane.count() == 2
    assert st.pageNames() == ["Statistics"]          # the two panes detached
    assert tc.visible_centre_tabs(app) == {"Plotting", "Saved Plots"}
    assert tc.is_tab_visible(app, "Saved Plots")
    assert tc.current_centre_tab(app) == "Plotting"  # pane A is focused


def test_exit_split_restores_notebook_order():
    from well_viewer.core import tab_controller as tc

    app, st, _ = _split_app()
    tc.enter_split(app, "Plotting", "Saved Plots")
    tc.exit_split(app)

    assert app._split_active is False
    assert app._split_pane.count() == 0
    assert st.pageNames() == ["Plotting", "Statistics", "Saved Plots"]
    assert st.setCurrentByName("Plotting") is True


def test_enter_split_same_tab_is_noop():
    from well_viewer.core import tab_controller as tc

    app, st, _ = _split_app()
    tc.enter_split(app, "Plotting", "Plotting")
    assert app._split_active is False
    assert st.pageNames() == ["Plotting", "Statistics", "Saved Plots"]


def test_focus_split_switches_focused_pane():
    from well_viewer.core import tab_controller as tc

    app, _, _ = _split_app()
    tc.enter_split(app, "Plotting", "Saved Plots")
    assert tc.current_centre_tab(app) == "Plotting"
    tc.focus_split(app, "Saved Plots")
    assert app._split_focused == "Saved Plots"
    assert tc.current_centre_tab(app) == "Saved Plots"


# ── Full-app integration (real WellViewerApp via the loaded_app fixture) ──────
_PLOT_LEAVES = {"Line Graphs", "Bar Plots", "Scatter Plot", "Distribution",
                "Heat Map"}


def test_real_app_enter_exit_split(loaded_app):
    """On a real app: splitting Plotting | Statistics force-builds Statistics,
    detaches both top-level pages (Plotting's nested notebook moves whole), and
    reports both leaves visible; exiting restores the notebook + order."""
    from well_viewer.core import tab_controller as tc

    app = loaded_app
    app._notebook.setCurrentByName("Plotting")
    app._enter_split("Plotting", "Statistics")

    assert app._split_active is True
    assert app._split_pane.count() == 2
    visible = tc.visible_centre_tabs(app)
    assert "Statistics" in visible
    assert visible & _PLOT_LEAVES           # Plotting resolved to a plot leaf
    assert app._current_centre_tab() in _PLOT_LEAVES  # pane A focused

    app._exit_split()
    assert app._split_active is False
    assert app._split_pane.count() == 0
    assert "Plotting" in app._notebook.pageNames()
    assert "Statistics" in app._notebook.pageNames()
