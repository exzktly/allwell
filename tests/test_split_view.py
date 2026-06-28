"""Split View — self-contained double-pane feature.

Pins the load-bearing invariants:
1. ``NamedPageStack.detachPage`` → ``reattachPage`` round-trips ``pageNames()``
   back to the original add order (the reparenting primitive).
2. ``install_split_view`` registers the provider hooks + entry-UI widgets, and
   ``enter_split`` / ``exit_split`` reparent two top-level pages into
   ``app._split_pane`` and back. While split, ``visible_centre_tabs`` reports
   both panes and ``current_centre_tab`` the focused one.
3. **Removability:** without ``install_split_view`` the provider hooks stay
   ``None`` and the resolvers collapse to single-tab — the app behaves exactly
   as if the feature didn't exist (no toggle button, one visible tab).

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


@pytest.fixture(autouse=True)
def _reset_split_providers():
    """Isolate the module-global provider hooks across tests."""
    from well_viewer.core import tab_controller as tc
    saved = (tc.split_focus_provider, tc.split_visible_provider,
             tc.split_nav_intercept)
    tc.split_focus_provider = tc.split_visible_provider = tc.split_nav_intercept = None
    try:
        yield
    finally:
        (tc.split_focus_provider, tc.split_visible_provider,
         tc.split_nav_intercept) = saved


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


# ── NamedPageStack reparenting primitive ─────────────────────────────────────

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


# ── Stub app + install ───────────────────────────────────────────────────────

def _split_app(*, install=True):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from well_viewer.core import split_view as sv

    st, widgets = _new_stack(["Plotting", "Statistics", "Saved Plots"])

    class _App:
        pass

    app = _App()
    app._notebook = st
    top = QWidget()
    QHBoxLayout(top)
    app._top_bar = top
    if install:
        sv.install_split_view(app)
    return app, st, widgets


def test_not_installed_is_single_tab():
    """Removability: with no install, the resolvers collapse to single-tab and
    no toggle button exists — the feature is fully absent."""
    from well_viewer.core import tab_controller as tc

    app, _, _ = _split_app(install=False)
    assert tc.split_visible_provider is None
    assert tc.split_focus_provider is None
    assert getattr(app, "_split_toggle_btn", None) is None
    assert tc.visible_centre_tabs(app) == {tc.current_centre_tab(app)}
    assert len(tc.visible_centre_tabs(app)) == 1


def test_install_registers_hooks_and_widgets():
    from well_viewer.core import tab_controller as tc

    app, _, _ = _split_app()
    assert tc.split_visible_provider is not None
    assert tc.split_focus_provider is not None
    assert tc.split_nav_intercept is not None
    assert getattr(app, "_split_toggle_btn", None) is not None
    assert getattr(app, "_split_pane", None) is not None


def test_enter_split_reparents_and_reports_both_panes():
    from well_viewer.core import split_view as sv
    from well_viewer.core import tab_controller as tc

    app, st, _ = _split_app()
    sv.enter_split(app, "Plotting", "Saved Plots")

    assert app._split_active is True
    assert app._split_pane.count() == 2
    assert st.pageNames() == ["Statistics"]          # the two panes detached
    assert tc.visible_centre_tabs(app) == {"Plotting", "Saved Plots"}
    assert tc.is_tab_visible(app, "Saved Plots")
    assert tc.current_centre_tab(app) == "Plotting"  # pane A is focused


def test_exit_split_restores_notebook_order():
    from well_viewer.core import split_view as sv

    app, st, _ = _split_app()
    sv.enter_split(app, "Plotting", "Saved Plots")
    sv.exit_split(app)

    assert app._split_active is False
    assert app._split_pane.count() == 0
    assert st.pageNames() == ["Plotting", "Statistics", "Saved Plots"]
    assert st.setCurrentByName("Plotting") is True


def test_enter_split_same_tab_is_noop():
    from well_viewer.core import split_view as sv

    app, st, _ = _split_app()
    sv.enter_split(app, "Plotting", "Plotting")
    assert app._split_active is False
    assert st.pageNames() == ["Plotting", "Statistics", "Saved Plots"]


def test_focus_split_switches_focused_pane():
    from well_viewer.core import split_view as sv
    from well_viewer.core import tab_controller as tc

    app, _, _ = _split_app()
    sv.enter_split(app, "Plotting", "Saved Plots")
    assert tc.current_centre_tab(app) == "Plotting"
    sv.focus_split(app, "Saved Plots")
    assert app._split_focused == "Saved Plots"
    assert tc.current_centre_tab(app) == "Saved Plots"


# ── Full-app integration (real WellViewerApp via the loaded_app fixture) ──────
_PLOT_LEAVES = {"Line Graphs", "Bar Plots", "Scatter Plot", "Distribution",
                "Heat Map"}


def test_real_app_enter_exit_split(loaded_app):
    """On a real app (install ran in build_ui): splitting Plotting | Statistics
    force-builds Statistics, detaches both top-level pages (Plotting's nested
    notebook moves whole), reports both leaves visible; exiting restores order."""
    from well_viewer.core import split_view as sv
    from well_viewer.core import tab_controller as tc

    app = loaded_app
    assert tc.split_visible_provider is not None  # install_split_view ran
    app._notebook.setCurrentByName("Plotting")
    sv.enter_split(app, "Plotting", "Statistics")

    assert app._split_active is True
    assert app._split_pane.count() == 2
    visible = tc.visible_centre_tabs(app)
    assert "Statistics" in visible
    assert visible & _PLOT_LEAVES               # Plotting resolved to a plot leaf
    assert app._current_centre_tab() in _PLOT_LEAVES  # pane A focused

    sv.exit_split(app)
    assert app._split_active is False
    assert app._split_pane.count() == 0
    assert "Plotting" in app._notebook.pageNames()
    assert "Statistics" in app._notebook.pageNames()


def test_dataset_reload_exits_split(loaded_app, mini_dataset):
    """Loading a dataset while split is active must leave split first — the load
    restores view-state + redraws against the single-notebook layout, which the
    detached panes break (reported crash: Image Table | plot, then open dataset)."""
    from well_viewer.core import split_view as sv

    app = loaded_app
    app._notebook.setCurrentByName("Plotting")
    sv.enter_split(app, "Plotting", "Image Browser")
    assert app._split_active is True

    app._load_path(mini_dataset)  # reopen the same dataset

    assert app._split_active is False
    assert app._split_pane.count() == 0
    # Notebook restored intact — no detached/dangling pages.
    assert "Plotting" in app._notebook.pageNames()
    assert "Image Browser" in app._notebook.pageNames()
