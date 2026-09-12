"""Split View — self-contained double-pane feature.

Pins the load-bearing invariants:
1. ``NamedPageStack.detachPage`` → ``reattachPage`` round-trips ``pageNames()``
   back to the original add order (the reparenting primitive).
2. ``install_split_view`` registers the provider hooks + entry-UI widgets, and
   ``enter_split`` / ``exit_split`` reparent two top-level pages into
   ``app._ctx.runtime.split.pane`` and back. While split, ``visible_centre_tabs`` reports
   both panes and ``current_centre_tab`` the focused one.
3. **Removability:** without ``install_split_view`` the provider hooks stay
   ``None`` and the resolvers collapse to single-tab — the app behaves exactly
   as if the feature didn't exist (no toggle button, one visible tab).

Qt is required, so the module skips when PySide6 isn't importable. ``offscreen``
keeps it headless. These tests are about *reparenting* — which widget ends up in which pane, and
what the notebook looks like afterwards. ``enter_split`` / ``exit_split`` /
``focus_split`` each call ``on_tab_change`` on the way out, and that function
redraws the active tab and rebuilds the sidebar, which needs the whole runtime.
The ``_no_tab_dispatch`` fixture below neutralises it for the duration, so the
stub app stays a stub.

That used to happen by accident: ``on_tab_change`` early-returned unless a Line
Graphs axis was registered, and an empty widget registry satisfied that. Line
Graphs is a droppable feature, so a build profile without it left that probe
false for the life of the process and no tab change reached any tab — the probe
became a readiness check on the centre notebook instead, and these tests had to
say out loud what they had been relying on.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_tab_dispatch(monkeypatch):
    """Neutralise ``on_tab_change`` for this module.

    Split View's enter/exit/focus each end by dispatching a tab change, which
    redraws the active tab and rebuilds the sidebar against the full runtime.
    Nothing in this file is testing that; patching the name in
    ``split_view``'s own namespace (it imports the function directly, so the
    module attribute is the binding the calls resolve) keeps these tests on
    the reparenting they exist to check.
    """
    from review.core import split_view as _sv
    monkeypatch.setattr(_sv, "on_tab_change", lambda *a, **k: None)


import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(autouse=True)
def _reset_split_providers():
    """Isolate the module-global provider hooks across tests."""
    from review.core import tab_controller as tc
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

    from review.core.centre_view import NamedPageStack

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

    from review.core import split_view as sv

    st, widgets = _new_stack(["Plotting", "Statistics", "Saved Plots"])

    class _App:
        pass

    app = _App()
    app._notebook = st
    top = QWidget()
    QHBoxLayout(top)
    app._top_bar = top
    # tab_controller's current_centre_tab/visible_centre_tabs + the split
    # providers now take ctx (A8): the centre notebook via ctx.window, the split
    # flags via ctx.runtime.split. Give the double a matching ctx.
    from types import SimpleNamespace
    from review.core.app_runtime import AppRuntime
    from review.core.widget_registry import WidgetRegistry
    app._ctx = SimpleNamespace(app=app, window=app, runtime=AppRuntime(app),
                               widgets=WidgetRegistry(app))
    if install:
        sv.install_split_view(app._ctx)
    return app, st, widgets


def test_not_installed_is_single_tab():
    """Removability: with no install, the resolvers collapse to single-tab and
    no toggle button exists — the feature is fully absent."""
    from review.core import tab_controller as tc

    app, _, _ = _split_app(install=False)
    assert tc.split_visible_provider is None
    assert tc.split_focus_provider is None
    assert app._ctx.runtime.split.toggle_btn is None
    assert tc.visible_centre_tabs(app._ctx) == {tc.current_centre_tab(app._ctx)}
    assert len(tc.visible_centre_tabs(app._ctx)) == 1


def test_install_registers_hooks_and_widgets():
    from review.core import tab_controller as tc

    app, _, _ = _split_app()
    assert tc.split_visible_provider is not None
    assert tc.split_focus_provider is not None
    assert tc.split_nav_intercept is not None
    assert app._ctx.runtime.split.toggle_btn is not None
    assert app._ctx.runtime.split.pane is not None


def test_toggle_hidden_outside_review_mode():
    """Regression guard: the split-view toggle lives in the top bar shared by
    all three modes (Review/Analyze/Datasets mount into the same
    ReviewApp central-pane stack, see allwell.py), but splitting two
    Review tabs makes no sense from Analyze or Datasets — the toggle used to
    just sit there regardless of mode, with nothing for it to act on.
    It must start visible on Review (the default mode), hide when the mode
    switches away, and reappear on switching back."""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

    from review.core import split_view as sv
    from review.core.app_runtime import AppRuntime
    from review.core.widget_registry import WidgetRegistry

    QApplication.instance() or QApplication([])

    class _ModedApp(QObject):
        modeChanged = Signal(str)

        def __init__(self):
            super().__init__()
            self._notebook = None  # install_split_view bails without one
            self._mode = "review"

        def currentMode(self):
            return self._mode

    app = _ModedApp()
    from review.core.centre_view import NamedPageStack
    app._notebook = NamedPageStack()
    top = QWidget()
    QHBoxLayout(top)
    app._top_bar = top
    from types import SimpleNamespace
    app._ctx = SimpleNamespace(app=app, window=app, runtime=AppRuntime(app),
                                widgets=WidgetRegistry(app))

    sv.install_split_view(app._ctx)
    btn = app._ctx.runtime.split.toggle_btn
    assert btn is not None
    assert btn.isHidden() is False  # visible on Review, the default mode

    app._mode = "analyze"
    app.modeChanged.emit("analyze")
    assert btn.isHidden() is True

    app._mode = "datasets"
    app.modeChanged.emit("datasets")
    assert btn.isHidden() is True

    app._mode = "review"
    app.modeChanged.emit("review")
    assert btn.isHidden() is False


def test_focus_watcher_reads_split_state_without_crashing():
    """Regression: ``_SplitFocusWatcher`` stores ``self._app`` (the window), so its
    focus slot must reach split state via ``self._app._ctx`` — not a bare
    ``self._ctx``, which the object doesn't have.

    Qt's ``focusChanged`` doesn't fire in the headless suite, so the slot was
    never exercised and a stale ``self._ctx`` reference (left by the ctx-migration)
    slipped through, crashing every real focus change while Split View was
    installed (``'_SplitFocusWatcher' object has no attribute '_ctx'``)."""
    from review.core.split_view import _SplitFocusWatcher

    app, _, _ = _split_app(install=False)
    watcher = _SplitFocusWatcher(app)
    # Split inactive → the slot reads ``self._app._ctx.runtime.split.active`` and
    # returns early. This must not raise AttributeError.
    watcher.on_focus_changed(None, None)


def test_section_nav_hands_ctx_not_app_to_intercept():
    """Regression: ``_on_section_nav_changed`` must pass the ViewerContext to the
    split nav-intercept, not the raw app.

    ``_nav_intercept`` reads ``ctx.runtime.split`` — a ctx-native hook — but it is
    registered *indirectly* (``tab_controller.split_nav_intercept`` module global),
    so the frozen-ctx guard can't resolve the call site and verify the argument.
    Passing the app instead of ``self._ctx`` crashed on every rail tab change
    (``'ReviewApp' object has no attribute 'runtime'``). This locks the
    contract without needing a full app.
    """
    from types import SimpleNamespace

    from review.core import tab_controller as tc
    from review.runtime_app import ReviewApp

    seen = {}

    def _spy(ctx, _key):
        seen["arg"] = ctx
        return False

    saved = tc.split_nav_intercept
    tc.split_nav_intercept = _spy
    try:
        sentinel_ctx = object()
        stub = SimpleNamespace(
            _section_nav_building=False,
            _ctx=sentinel_ctx,
            _notebook=SimpleNamespace(setCurrentByName=lambda _k: None),
        )
        # Call the unbound method against the stub — exercises the real caller body.
        ReviewApp._on_section_nav_changed(stub, "Line Graphs")
        assert seen["arg"] is sentinel_ctx, (
            "the nav-intercept hook must receive ctx (ctx.runtime.split), not the app"
        )
    finally:
        tc.split_nav_intercept = saved


def test_enter_split_reparents_and_reports_both_panes():
    from review.core import split_view as sv
    from review.core import tab_controller as tc

    app, st, _ = _split_app()
    sv.enter_split(app._ctx, "Plotting", "Saved Plots")

    assert app._ctx.runtime.split.active is True
    assert app._ctx.runtime.split.pane.count() == 2
    assert st.pageNames() == ["Statistics"]          # the two panes detached
    assert tc.visible_centre_tabs(app._ctx) == {"Plotting", "Saved Plots"}
    assert tc.is_tab_visible(app._ctx, "Saved Plots")
    assert tc.current_centre_tab(app._ctx) == "Plotting"  # pane A is focused


def test_exit_split_restores_notebook_order():
    from review.core import split_view as sv

    app, st, _ = _split_app()
    sv.enter_split(app._ctx, "Plotting", "Saved Plots")
    sv.exit_split(app._ctx)

    assert app._ctx.runtime.split.active is False
    assert app._ctx.runtime.split.pane.count() == 0
    assert st.pageNames() == ["Plotting", "Statistics", "Saved Plots"]
    assert st.setCurrentByName("Plotting") is True


def test_enter_split_same_tab_is_noop():
    from review.core import split_view as sv

    app, st, _ = _split_app()
    sv.enter_split(app._ctx, "Plotting", "Plotting")
    assert app._ctx.runtime.split.active is False
    assert st.pageNames() == ["Plotting", "Statistics", "Saved Plots"]


def test_focus_split_switches_focused_pane():
    from review.core import split_view as sv
    from review.core import tab_controller as tc

    app, _, _ = _split_app()
    sv.enter_split(app._ctx, "Plotting", "Saved Plots")
    assert tc.current_centre_tab(app._ctx) == "Plotting"
    sv.focus_split(app._ctx, "Saved Plots")
    assert app._ctx.runtime.split.focused == "Saved Plots"
    assert tc.current_centre_tab(app._ctx) == "Saved Plots"


# ── Full-app integration (real ReviewApp via the loaded_app fixture) ──────
_PLOT_LEAVES = {"Line Graphs", "Bar Plots", "Scatter Plot", "Distribution",
                "Heat Map"}


def test_real_app_enter_exit_split(loaded_app):
    """On a real app (install ran in build_ui): splitting Plotting | Statistics
    force-builds Statistics, detaches both top-level pages (Plotting's nested
    notebook moves whole), reports both leaves visible; exiting restores order."""
    from review.core import split_view as sv
    from review.core import tab_controller as tc

    app = loaded_app
    assert tc.split_visible_provider is not None  # install_split_view ran
    app._notebook.setCurrentByName("Plotting")
    sv.enter_split(app._ctx, "Plotting", "Statistics")

    assert app._ctx.runtime.split.active is True
    assert app._ctx.runtime.split.pane.count() == 2
    visible = tc.visible_centre_tabs(app._ctx)
    assert "Statistics" in visible
    assert visible & _PLOT_LEAVES               # Plotting resolved to a plot leaf
    assert app._current_centre_tab() in _PLOT_LEAVES  # pane A focused

    sv.exit_split(app._ctx)
    assert app._ctx.runtime.split.active is False
    assert app._ctx.runtime.split.pane.count() == 0
    assert "Plotting" in app._notebook.pageNames()
    assert "Statistics" in app._notebook.pageNames()


def test_dataset_reload_exits_split(loaded_app, mini_dataset):
    """Loading a dataset while split is active must leave split first — the load
    restores view-state + redraws against the single-notebook layout, which the
    detached panes break (reported crash: Image Table | plot, then open dataset)."""
    from review.core import split_view as sv

    app = loaded_app
    app._notebook.setCurrentByName("Plotting")
    sv.enter_split(app._ctx, "Plotting", "Image Browser")
    assert app._ctx.runtime.split.active is True

    app._load_path(mini_dataset)  # reopen the same dataset

    assert app._ctx.runtime.split.active is False
    assert app._ctx.runtime.split.pane.count() == 0
    # Notebook restored intact — no detached/dangling pages.
    assert "Plotting" in app._notebook.pageNames()
    assert "Image Browser" in app._notebook.pageNames()
