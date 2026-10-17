"""The left-rail collapse toggle drives whichever sidebar matches the mode.

``apply_sidebar_visibility`` is the single chokepoint for the ``Ctrl+B`` / top-bar
toggle and the Review/Analyze mode switch. It must collapse symmetrically: the
Review rail in Review mode, and the Analyze tab's own form sidebar in Analyze
mode (the bug: the toggle previously only affected Review).

``isVisibleTo(parent)`` reflects a widget's own visible flag independent of
whether the top-level is actually shown, so it's stable under offscreen Qt.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from review.core.tab_controller import apply_sidebar_visibility


def _make(mode: str, collapsed: bool):
    from PySide6.QtWidgets import QApplication, QWidget

    QApplication.instance() or QApplication([])
    host = QWidget()
    review_rail = QWidget(host)
    analyze_rail = QWidget(host)

    class _Analyze:
        pass

    class _App:
        def currentMode(self):
            return mode

    from review.core.widget_registry import WidgetRegistry

    app = _App()
    app.window = app
    # The real context's registry: phone layout's drawer is looked up in it
    # (``phone_drawer.apply_phone_drawer``, which this chokepoint calls last).
    app.widgets = WidgetRegistry(app)
    app._sidebar_root = review_rail
    app._sidebar_collapsed = collapsed
    az = _Analyze()
    az._left_sidebar = analyze_rail
    app._analyze_pane = az
    return app, host, review_rail, analyze_rail


@pytest.mark.parametrize("mode,collapsed,review_vis,analyze_vis", [
    ("review", False, True, True),    # review rail shown; analyze rail ready
    ("review", True, False, False),   # collapsed hides both
    ("analyze", False, False, True),  # analyze rail shown; review rail hidden
    ("analyze", True, False, False),  # collapsed hides both
])
def test_toggle_collapses_active_mode_sidebar(mode, collapsed, review_vis, analyze_vis):
    app, host, review_rail, analyze_rail = _make(mode, collapsed)
    apply_sidebar_visibility(app)
    assert review_rail.isVisibleTo(host) is review_vis
    assert analyze_rail.isVisibleTo(host) is analyze_vis


def test_missing_analyze_pane_is_safe():
    """No Analyze pane mounted (e.g. Review-only contexts) → no crash."""
    app, host, review_rail, _ = _make("review", False)
    app._analyze_pane = None
    apply_sidebar_visibility(app)  # must not raise
    assert review_rail.isVisibleTo(host) is True
