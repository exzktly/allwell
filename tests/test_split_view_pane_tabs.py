"""Split View gives each pane its own sub-tab strip.

The shared band above the centre has one slot, so while two pages are on screen
it can name only one of them. Pointing it at the focused pane drew nothing at
all for the other page, and splitting *from* a page with no sub-tabs (Statistics,
Spatial, smFISH, Review CSV, Saved Plots, Multivariate) drew no strip for either
pane — the reported symptom. Each pane now borrows its page's strip out of the
band and mounts it in its own header, and hands it back on exit.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


def _band_bar(app, name):
    """*name*'s tab bar, found through the band page that hosts it.

    Deliberately not read off ``_page_tabs_bar_by_name``: this file has to be
    able to fail against the code it guards, and that mapping is part of the
    fix. The band page's layout is stretch-then-bar either way.
    """
    page = (getattr(app, "_page_tabs_by_name", None) or {}).get(name)
    if page is None or page.layout() is None:
        return None
    item = page.layout().itemAt(page.layout().count() - 1)
    return item.widget() if item is not None else None


def _build_all_pages(app) -> None:
    """Force every deferred page body so its tab strip is registered."""
    build = app._ctx.runtime.centre.build_pending
    for name in [n for n in app._notebook.pageNames() if n]:
        build(name)


def _pane_of(app, widget):
    """The split pane containing *widget*, by name, or ``None``."""
    from PySide6.QtWidgets import QWidget

    panes = app._ctx.runtime.split.pane_widgets or {}
    node = widget
    while isinstance(node, QWidget):
        for top, container in panes.items():
            if node is container:
                return top
        node = node.parentWidget()
    return None


def _band_is_blank(app) -> bool:
    host = app._page_tabs_host
    return host.currentWidget() is host.widget(0)


def test_each_pane_draws_its_own_tab_strip(loaded_app):
    """Both panes' strips are on screen, each inside the pane it drives."""
    from well_viewer.core import split_view as sv

    app = loaded_app
    _build_all_pages(app)
    bars = {n: _band_bar(app, n) for n in ("Plotting", "Home")}
    assert all(bars.values())

    sv.enter_split(app._ctx, "Plotting", "Home")
    try:
        assert _pane_of(app, bars["Plotting"]) == "Plotting"
        assert _pane_of(app, bars["Home"]) == "Home"
        # The band cannot carry two strips, so it carries neither.
        assert _band_is_blank(app)
    finally:
        sv.exit_split(app._ctx)


def test_pane_b_keeps_its_strip_when_pane_a_has_none(loaded_app):
    """The reported case: split from a page with no sub-tabs of its own.

    The band followed the focused pane, which is pane A, so Plotting's strip was
    drawn nowhere — and Statistics' pane, having no strip to focus into, offered
    no way to bring it back.
    """
    from well_viewer.core import split_view as sv

    app = loaded_app
    _build_all_pages(app)
    # Statistics genuinely has no sub-tabs of its own.
    assert _band_bar(app, "Statistics") is None
    plotting_bar = _band_bar(app, "Plotting")

    sv.enter_split(app._ctx, "Statistics", "Plotting")
    try:
        assert _pane_of(app, plotting_bar) == "Plotting"
    finally:
        sv.exit_split(app._ctx)


def test_exit_hands_the_strips_back_to_the_band(loaded_app):
    """After exiting, the borrowed bars are in the band again and it shows one."""
    from well_viewer.core import split_view as sv

    app = loaded_app
    _build_all_pages(app)
    bars = {n: _band_bar(app, n) for n in ("Plotting", "Home")}

    sv.enter_split(app._ctx, "Plotting", "Home")
    sv.exit_split(app._ctx)

    for name, bar in bars.items():
        assert bar.parentWidget() is app._page_tabs_by_name[name]
        assert _band_bar(app, name) is bar
    app._notebook.setCurrentByName("Plotting")
    from well_viewer.core.app_shell_view import show_page_tabs

    show_page_tabs(app, "Plotting")
    assert app._page_tabs_host.currentWidget() is app._page_tabs_by_name["Plotting"]
