"""The one inspector of a Review window, and the only way features reach it.

There is **one inspector per Review window**, not one per page: that is what
makes "one surface, in one place" true. It is a
:class:`widgets.inspector.Inspector` with four tabs — **Data**, **Display**,
**Style**, **Detail** — and it is hosted in one of two places:

* **At the desk**, a column at the right of the splitter Review's pages sit in
  (:class:`widgets.docked_panel.DockedPanel`). It stays open while the user
  works, its width is the user's and is remembered, and the page is laid out
  beside it rather than under it.
* **In phone layout**, the Controls sheet. The application shell owns that
  sheet (``ui/phone_shell.py``) and registers itself here as the *phone host*
  (:func:`set_phone_host`), because ``ui/`` may not import a pane and this
  module may not import the shell.

Features call the functions below and nothing else: :func:`push` to show a
sub-option as a page, :func:`open_tab` / :func:`toggle_tab` for the fixed
Display and Style buttons, :func:`show_detail` for the thing that was picked,
:func:`close`. None of them asks which layout is on.

**The inspector serves one page at a time.** :func:`sync` is called on every
tab change (``tab_controller.on_tab_change``): when the page on screen is no
longer the one the inspector was filled from, everything lent goes back to that
page and the inspector is filled again from the new one through the registered
*root finders* — callables that return the new page's first-page widget for a
tab (the page's Display panel, the Style panel). In Split View the page served
is the focused pane's, and pressing a control in the other pane focuses that
pane first (:func:`serve_widget`), so the inspector never shows one pane's
options under the other pane's name.

The window's host object is registered in Review's widget registry under
:data:`KEY`, the way the phone's rail drawer is, so nothing here is an
attribute on the window.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QWidget

from core import phone_layout
from core import app_settings as _settings
from core.safe import swallowed
from ui import theme
from widgets.docked_panel import DockedPanel
from widgets.inspector import Inspector

#: The widget-registry key of a window's :class:`InspectorHost`.
KEY = "_inspector_host"

#: The tabs, in order. Every window has all four on every page; one a page
#: cannot fill is disabled, never removed.
TABS: tuple[tuple[str, str], ...] = (
    ("data", "Data"), ("display", "Display"), ("style", "Style"), ("detail", "Detail"),
)

#: Tabs whose first page takes the tab's whole height: the Style panel is a
#: list that scrolls itself.
FILLED_TABS = frozenset({"style"})

#: The settings key the desk column's width is remembered under.
WIDTH_SETTING = "review/inspector_width"

#: The desk column's narrowest and widest, in pixels.
MIN_WIDTH, MAX_WIDTH = 280, 640

#: ``fn(ctx, leaf) -> {tab: widget}``: a page's first-page widget for each tab
#: it has one for. Registered once, at import, by the module that knows how to
#: find them (:func:`add_root_finder`).
RootFinder = Callable[[Any, str], dict]
_root_finders: list[RootFinder] = []


def add_root_finder(fn: RootFinder) -> None:
    if fn not in _root_finders:
        _root_finders.append(fn)


class InspectorHost(QObject):
    """A window's inspector, the desk column it docks in, and which page it
    is serving."""

    def __init__(self, ctx) -> None:
        super().__init__(ctx.window)
        self._ctx = ctx
        self.inspector = Inspector()
        self.inspector.setTabs(list(TABS))
        self.inspector.closeRequested.connect(lambda: close(ctx))
        #: The leaf the inspector was last filled from.
        self.leaf = ""
        #: Whether the user wants the desk column open: it closes itself on a
        #: page with nothing to show and returns on the next page that has.
        self.wanted = False
        self.dock = None
        #: The phone's Controls sheet, as the shell registered it.
        self.phone = None
        self._listener = self._on_layout
        phone_layout.add_listener(self._listener)
        self.destroyed.connect(lambda *_: phone_layout.remove_listener(self._listener))

    def _on_layout(self, _on: bool) -> None:
        """The layout switched: everything lent goes home before the rescale
        pass walks the widgets, and the desk column leaves the splitter."""
        try:
            self.inspector.giveBackAll()
            self.leaf = ""
            if self.dock is not None and self.dock.isOpen():
                self.dock.closeNow()
        except RuntimeError:
            phone_layout.remove_listener(self._listener)   # the window went first

    # ── the desk column ──────────────────────────────────────────────────
    def desk_dock(self) -> DockedPanel | None:
        if self.dock is not None and shiboken6.isValid(self.dock):
            return self.dock
        split = getattr(self._ctx.window, "_h_pane", None)
        if split is None:
            return None
        self.dock = DockedPanel(split, self._desk_width, side="right", resizable=True,
                                min_width=MIN_WIDTH, max_width=MAX_WIDTH)
        # Its look is the window's (``ui/shell_frame``): in window style W1 the inspector is a panel of its own.
        self.dock.setObjectName("InspectorDock")
        self.dock.widthChanged.connect(self._remember_width)
        return self.dock

    def _desk_width(self) -> int:
        try:
            saved = int(_settings.app_settings().value(WIDTH_SETTING, 0) or 0)
        except (TypeError, ValueError):
            saved = 0
        return max(MIN_WIDTH, min(MAX_WIDTH, saved or theme.Metrics.dock_w))

    def _remember_width(self, width: int) -> None:
        _settings.app_settings().setValue(WIDTH_SETTING, int(width))


def host(ctx) -> InspectorHost:
    """The window's inspector host, built on first use."""
    if ctx.widgets.has(KEY):
        return ctx.widgets.get(KEY)
    return ctx.widgets.register(KEY, InspectorHost(ctx))


def inspector(ctx) -> Inspector:
    return host(ctx).inspector


def set_phone_host(ctx, phone) -> None:
    """Register the phone's Controls sheet: an object with ``open_inspector()``,
    ``close_inspector()`` and ``inspector_is_open()``. The shell calls this."""
    host(ctx).phone = phone


def _on_phone(h: InspectorHost) -> bool:
    return h.phone is not None and phone_layout.is_on()


def is_open(ctx) -> bool:
    if not ctx.widgets.has(KEY):
        return False
    h = host(ctx)
    if _on_phone(h):
        return bool(h.phone.inspector_is_open())
    return h.dock is not None and shiboken6.isValid(h.dock) and h.dock.isOpen()


# ── which page ───────────────────────────────────────────────────────────────

def current_leaf(ctx) -> str:
    from review.core.tab_controller import current_centre_tab
    return current_centre_tab(ctx)


def serve_widget(ctx, widget: QWidget | None) -> None:
    """Make the page *widget* belongs to the one the inspector serves.

    Outside Split View there is one page and nothing to do. In Split View a
    press on a button does not move the focus (a button is not pane content),
    so the pane the pressed control sits in is focused here, before its page is
    pushed."""
    if widget is None or not ctx.runtime.split.active:
        return
    from review.core import split_view
    top = split_view._resolve_focused_pane(ctx, widget)
    if top:
        split_view.focus_split(ctx, top)


def _fill_roots(ctx, h: InspectorHost) -> None:
    """Give the inspector the first pages of the page on screen."""
    leaf = current_leaf(ctx)
    insp = h.inspector
    if leaf != h.leaf:
        insp.popToRoot()
        for key, _t in TABS:
            if key != "data" or not _on_phone(h):
                insp.setTabWidget(key, None)
        h.leaf = leaf
    insp.setTitle(leaf)
    for finder in list(_root_finders):
        try:
            found = finder(ctx, leaf) or {}
        except RuntimeError:
            swallowed()  # a page torn down while it was asked
            continue
        for key, widget in found.items():
            if widget is not None and insp.tabWidget(key) is None:
                insp.setTabWidget(key, widget, fill=key in FILLED_TABS)


def fill(ctx) -> None:
    """Fill the inspector from the page on screen. The phone's Controls sheet
    calls this as it opens, after lending the page's band as the Data tab."""
    _fill_roots(ctx, host(ctx))


def sync(ctx) -> None:
    """Follow a page change: give the last page its widgets back, and — while
    the inspector is open, or wanted at the desk — fill it from the new one."""
    if not ctx.widgets.has(KEY):
        return
    h = host(ctx)
    leaf = current_leaf(ctx)
    if leaf == h.leaf:
        return
    if _on_phone(h):
        # The shell lends the band and closes its sheet on a page change; the
        # pages and roots pushed here go back before it does.
        h.inspector.popToRoot()
        for key, _t in TABS:
            if key != "data":
                h.inspector.setTabWidget(key, None)
        h.leaf = ""
        return
    if not (is_open(ctx) or h.wanted):
        h.inspector.giveBackAll()
        h.leaf = ""
        return
    _fill_roots(ctx, h)
    if _first_usable(h.inspector) is None:
        _close_desk(h)                    # nothing to show here; ``wanted`` holds
    elif h.wanted and not is_open(ctx):
        _open_desk(h)
        h.inspector.showTab(_first_usable(h.inspector))
    elif not h.inspector.isTabEnabled(h.inspector.currentTab() or ""):
        h.inspector.showTab(_first_usable(h.inspector))


def _first_usable(insp: Inspector) -> str | None:
    return next((k for k, _t in insp.tabs() if insp.isTabEnabled(k)), None)


# ── opening and closing ──────────────────────────────────────────────────────

def _open_desk(h: InspectorHost) -> bool:
    dock = h.desk_dock()
    if dock is None:
        return False
    if h.inspector.parentWidget() is not dock:
        dock.setContentWidget(h.inspector)     # first use, or back from the phone's sheet
    h.inspector.show()
    dock.open()
    return True


def _close_desk(h: InspectorHost) -> None:
    if h.dock is not None and shiboken6.isValid(h.dock) and h.dock.isOpen():
        h.dock.close()


def _open_host(ctx, h: InspectorHost) -> bool:
    if _on_phone(h):
        return bool(h.phone.open_inspector())
    return _open_desk(h)


def open_tab(ctx, tab: str | None = None, *, anchor: QWidget | None = None) -> bool:
    """Open the inspector on *tab*'s first page (the first tab it can show
    when ``None``). ``False`` when the page has nothing for that tab."""
    serve_widget(ctx, anchor)
    h = host(ctx)
    _fill_roots(ctx, h)
    insp = h.inspector
    tab = tab or (insp.currentTab() if insp.isTabEnabled(insp.currentTab() or "") else None) \
        or _first_usable(insp)
    if tab is None or not insp.isTabEnabled(tab):
        return False
    if not _open_host(ctx, h):
        return False
    h.wanted = True
    insp.popToRoot(tab)
    insp.showTab(tab)
    return True


def open_tab_with(ctx, tab: str, widget: QWidget, *, fill: bool = False,
                  anchor: QWidget | None = None) -> bool:
    """Open the inspector on *tab* with *widget* as that tab's first page —
    for a tab whose content is built on demand (the Style panel of the figure
    whose button was pressed)."""
    serve_widget(ctx, anchor)
    h = host(ctx)
    _fill_roots(ctx, h)
    insp = h.inspector
    if insp.tabWidget(tab) is not widget:
        insp.setTabWidget(tab, None)
        insp.setTabWidget(tab, widget, fill=fill)
    if not _open_host(ctx, h):
        return False
    h.wanted = True
    insp.popToRoot(tab)
    insp.showTab(tab)
    return True


def shows_root(ctx, tab: str, widget: QWidget) -> bool:
    """Whether the inspector is open on *tab*'s first page and it is *widget*."""
    if not is_open(ctx):
        return False
    insp = inspector(ctx)
    return insp.currentTab() == tab and insp.depth() == 0 and insp.tabWidget(tab) is widget


def toggle_tab(ctx, tab: str, *, anchor: QWidget | None = None) -> bool:
    """The fixed Display and Style buttons: open on *tab*, or close when the
    inspector already shows that tab's first page. Returns whether it is open."""
    if is_open(ctx):
        insp = inspector(ctx)
        if insp.currentTab() == tab and insp.depth() == 0 and host(ctx).leaf == current_leaf(ctx):
            close(ctx)
            return False
    return open_tab(ctx, tab, anchor=anchor)


def push(ctx, key: str, title: str, widget: QWidget, *, tab: str = "display",
         large: bool = False, anchor: QWidget | None = None) -> bool:
    """Show *widget* as a page of the inspector, opening it if it is closed.

    *anchor* is the control that was pressed; in Split View its pane becomes
    the one served. Pushing the page already on show closes it again when it
    is the only thing the inspector was opened for, so a band's value button
    works as a toggle."""
    serve_widget(ctx, anchor)
    h = host(ctx)
    _fill_roots(ctx, h)
    insp = h.inspector
    if not _open_host(ctx, h):
        return False
    h.wanted = True
    insp.pushPage(key, title, widget, tab=tab, large=large)
    return True


def is_showing(ctx, widget: QWidget) -> bool:
    """Whether *widget* is the page on show in an open inspector."""
    return is_open(ctx) and inspector(ctx).currentWidget() is widget


def pop(ctx) -> None:
    if ctx.widgets.has(KEY):
        inspector(ctx).popPage()


def close(ctx) -> None:
    """Close the inspector and give back everything it was lent."""
    if not ctx.widgets.has(KEY):
        return
    h = host(ctx)
    h.wanted = False
    if _on_phone(h):
        h.phone.close_inspector()
        return
    h.inspector.giveBackAll()
    h.leaf = ""
    _close_desk(h)


def show_detail(ctx, widget: QWidget, title: str, *, anchor: QWidget | None = None,
                fill: bool = False, reveal: bool = True) -> bool:
    """Show *widget* on the Detail tab — the one thing just picked. Picking
    another calls this again with the same widget, updated by its owner.

    *fill* gives the widget the tab's whole height (a figure, which has no
    height of its own to ask for). With *reveal* false the widget becomes the
    Detail tab's without the inspector being opened or brought to that tab:
    for an owner redrawing the thing already picked, which must not re-open an
    inspector the user closed."""
    serve_widget(ctx, anchor)
    h = host(ctx)
    _fill_roots(ctx, h)
    insp = h.inspector
    insp.setTabWidget("detail", widget, fill=fill)
    if not reveal:
        return insp.tabWidget("detail") is widget
    if not _open_host(ctx, h):
        return False
    h.wanted = True
    insp.popToRoot("detail")
    insp.showTab("detail")
    return True


def clear_detail(ctx) -> None:
    """Nothing is picked any more: the Detail tab empties and is disabled."""
    if not ctx.widgets.has(KEY):
        return
    insp = inspector(ctx)
    was_on = insp.currentTab() == "detail"
    insp.setTabWidget("detail", None)
    if was_on and is_open(ctx) and _first_usable(insp) is None:
        close(ctx)


def sync_soon(ctx) -> None:
    """:func:`sync` once the tab change that called this has finished."""
    QTimer.singleShot(0, lambda: _sync_if_alive(ctx))


def _sync_if_alive(ctx) -> None:
    try:
        if shiboken6.isValid(ctx.window):
            sync(ctx)
    except RuntimeError:
        swallowed()  # the window went before the queued sync ran
