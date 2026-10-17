"""Phone layout through the real window: the real button, the real panes.

Slow lane — this constructs the whole ``AllWellApp`` once for the file, and
each switch into or out of phone layout re-applies the application stylesheet
(about 6 s on a built app). ``tests/test_phone_layout.py`` pins the parts in
the fast lane; this file is the one that would notice the parts not being
wired together — a button connected to nothing, a pane whose sidebar comes
back on the next resize, a desktop that does not come back at all.

The offscreen screen is 800×800, narrower than the desk window's 1100 minimum,
so it is phone-shaped: the window must fit it in either layout, and Auto picks
the 1.25 preset (800 / 600 = 1.33).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow


def _pump(qapp, n: int = 15) -> None:
    for _ in range(n):
        qapp.processEvents()


@pytest.fixture(scope="module")
def win(qapp):
    from allwell import AllWellApp
    w = AllWellApp(None)
    w.show()
    _pump(qapp)
    yield w
    from core import phone_layout
    from ui import theme
    phone_layout.set_on(False)
    theme.apply_ui_scale(1.0)
    w.close()


def _visible(w) -> bool:
    return w is not None and w.isVisibleTo(w.window())


def test_phone_layout_round_trip_through_the_real_controls(qapp, win):
    from core import phone_layout
    from ui import theme

    review = win._review
    shell = win._phone
    avail = win.screen().availableGeometry()

    # At the desk, on a phone-shaped screen: the window fits it and the shape
    # is offered — never acted on.
    assert win.minimumWidth() <= avail.width()
    assert shell._banner is not None and _visible(shell._banner)
    assert not phone_layout.is_on()

    shell.desk_button.click()
    _pump(qapp)
    assert phone_layout.is_on()
    assert theme.ui_scale() == 1.25
    assert not _visible(win._header)
    assert not _visible(shell._banner)
    for w in (win._present_btn, win._reset_saves_btn, win._copy_analysis_btn,
              win._open_btn, win._reset_view_btn, *win._status_hints):
        assert not _visible(w), w
    assert _visible(win._refresh_btn) and _visible(win._help_btn)
    assert _visible(win._mode_seg)
    assert all(not _visible(s._icon_lbl) for s in win._mode_seg._segments)
    assert not _visible(review._page_tabs_host)
    assert (win.minimumWidth(), win.minimumHeight()) <= (320, 400)
    drawer = review._ctx.widgets.get("_phone_drawer")
    # Content first: the plots, and the rail tucked away in the drawer.
    assert not drawer.isVisible()
    assert _visible(review._central_pane_stack)
    assert not shell.menu_button.isChecked()

    # ☰ slides the whole rail — sections and well plate — out over the page,
    # which stays where it is underneath.
    shell.menu_button.click()
    _pump(qapp)
    assert drawer.isOpen() and drawer.isVisible()
    assert review._sidebar_root.parentWidget() is drawer.contentWidget()
    assert _visible(review._section_nav) and _visible(review._sidebar_plate_slot)
    assert _visible(review._central_pane_stack)
    assert shell.menu_button.isChecked()
    # Below the band, so ☰ and the mode bar stay in reach.
    assert drawer.geometry().top() >= review._h_pane.geometry().top()

    # Code that used to put the desktop chrome back on its own: a resize
    # re-parks (and re-shows) the rail toggle, a mode switch re-shows Split.
    win.resize(win.width() - 10, win.height() - 10)
    _pump(qapp)
    assert not _visible(review._sidebar_toggle_btn)

    # The dimmed strip closes it, and the collapse flag and ☰ follow.
    drawer._backdrop.clicked.emit()
    _pump(qapp)
    assert not drawer.isOpen()
    assert review._sidebar_collapsed
    assert not shell.menu_button.isChecked()

    # A section tapped in the rail closes it too.
    shell.menu_button.click()
    _pump(qapp)
    key = next(k for k in review._section_nav.items() if k != review._section_nav.currentKey())
    review._section_nav._rows[key].clicked.emit(key)
    _pump(qapp)
    assert not drawer.isOpen()
    assert review._section_nav.currentKey() == key

    # Analyze: the run dashboard first, stacked; ☰ slides the form out.
    win._mode_seg.setCurrentIndex(1)
    _pump(qapp)
    analyze = win._analyze
    assert analyze._dashboard.isStacked()
    assert not drawer.isOpen()
    split_btn = getattr(review._ctx.runtime.split, "toggle_btn", None)
    shell.menu_button.click()
    _pump(qapp)
    assert drawer.isOpen()
    assert analyze._left_sidebar.parentWidget() is drawer.contentWidget()
    assert _visible(analyze._left_sidebar) and not _visible(review._sidebar_root)
    # Picking a mode with the rail out closes it.
    win._on_titlebar_mode_seg(0)
    _pump(qapp)
    assert not drawer.isOpen()
    assert split_btn is None or not _visible(split_btn)
    assert not _visible(review._sidebar_toggle_btn)

    # The Aa menu: the text size, and the way back.
    shell._fill_menu()
    texts = [a.text() for a in shell._menu.actions()]
    assert "Desktop layout" in texts and "Text size" in texts
    assert [a.text() for a in shell._size_menu.actions()][0] == "Auto (125 %)"
    next(a for a in shell._menu.actions() if a.text() == "Desktop layout").trigger()
    _pump(qapp)

    assert not phone_layout.is_on()
    assert theme.ui_scale() == 1.0
    assert _visible(win._header)
    for w in (win._present_btn, win._reset_view_btn, review._section_nav,
              review._page_tabs_host, review._sidebar_root,
              review._central_pane_stack, review._sidebar_toggle_btn):
        assert _visible(w), w
    for w in (shell.menu_button, shell.page_button, shell.options_button):
        assert not _visible(w), w
    # Every left panel is back in its own splitter, rail first.
    assert review._h_pane.indexOf(review._sidebar_root) == 0
    assert analyze._left_sidebar.parentWidget() is not drawer.contentWidget()
    assert not drawer.isVisible()
    assert (review._sidebar_root.minimumWidth(),
            review._sidebar_root.maximumWidth()) == (theme.Metrics.rail_w,) * 2
    assert all(_visible(s._icon_lbl) for s in win._mode_seg._segments)
    assert not analyze._dashboard.isStacked()
    assert split_btn is None or _visible(split_btn)
    # The desk minimum, as far as this screen allows it.
    assert (win.minimumWidth(), win.minimumHeight()) == (
        min(phone_layout.DESK_MIN_W, avail.width()),
        min(phone_layout.DESK_MIN_H, avail.height()))


def test_closing_in_phone_layout_mid_run_asks_first(qapp, win, monkeypatch):
    import allwell
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QMessageBox

    asked = []
    monkeypatch.setattr(allwell, "_analyze_run_busy", lambda _a: True)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a) or QMessageBox.Cancel)
    win._phone.set_on(True, restyle=False)
    try:
        ev = QCloseEvent()
        win.closeEvent(ev)
        assert asked and not ev.isAccepted()
    finally:
        win._phone.set_on(False, restyle=False)
