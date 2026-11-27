"""Pages of the inspector that every feature shows the same way.

A feature that lets the user pick several of a list — timepoints, series,
fields of view, gates — shows ``widgets.multi_select.MultiSelect`` as a page of
the window's inspector through :func:`open_multi_select`, and nothing else: no
menu of checkboxes, no dropdown of its own. The page applies as a box is
ticked, and the button that opened it goes on reading the choice.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import shiboken6
from PySide6.QtWidgets import QWidget

from review.core import inspector_service
from widgets.multi_select import MultiSelect

#: What a picker and its button carry, as attributes on the widgets
#: themselves: the button its pickers by key, the picker the callable its
#: ``changed`` reaches (swapped on every open, not stacked) and its settle
#: timer. Nothing here is a module-level mapping: a handler closes over the
#: context, the context reaches the button through the widget registry, and a
#: mapping from picker to handler would then hold every window ever opened.
_PICKERS, _HANDLER, _SETTLE = "_aw_pickers", "_aw_picker_handler", "_aw_picker_settle"


def pickers_of(anchor: QWidget) -> dict:
    """``{key: picker}`` for the multi-select pages *anchor* has opened."""
    mine = getattr(anchor, _PICKERS, None)
    if mine is None:
        mine = {}
        setattr(anchor, _PICKERS, mine)
    return mine


def _picker_for(anchor: QWidget, key: str, empty_text: str) -> MultiSelect:
    # Not ``anchor.findChild``: while its page is on show the picker is the
    # inspector's child, not the anchor's, and a second press would then build
    # a second picker and stack a second page.
    mine = pickers_of(anchor)
    picker = mine.get(key)
    if picker is None or not shiboken6.isValid(picker):
        picker = MultiSelect(anchor, empty_text=empty_text)
        picker.setObjectName(f"MultiSelect:{key}")
        picker.hide()
        picker.changed.connect(lambda keys, p=picker: getattr(p, _HANDLER)(keys))
        mine[key] = picker
    return picker


def open_multi_select(ctx, anchor: QWidget, *, key: str, title: str, items: Iterable,
                      checked: Iterable, on_change: Callable[[list], None],
                      tab: str = "data", settle_ms: int = 0,
                      empty_text: str = "Nothing to choose from.") -> MultiSelect | None:
    """Show a multi-select as a page of the inspector, from the control *anchor*.

    *items* are ``(key, label)`` pairs or strings and *checked* the keys ticked
    now; *on_change* is called with the checked keys on every tick and once
    for All and for None. Pressing *anchor* again while its page is on show
    leaves the page (and ``None`` is returned). The picker is *anchor*'s
    child, refilled on every open, so it always shows the list as it is now.

    *settle_ms* is for a choice whose application is expensive (a pass over
    every cell, behind a progress popup): *on_change* then runs once, that long
    after the last tick of a burst — and at once when the page is left with a
    change still pending, so leaving never loses a choice. It still applies as
    the user ticks, without a close to wait for; it does not apply per tick."""
    if anchor is None:
        return None
    picker = _picker_for(anchor, key, empty_text)
    if inspector_service.is_showing(ctx, picker):
        inspector_service.pop(ctx)
        return None
    _flush(picker)
    setattr(picker, _HANDLER,
            _settled(ctx, picker, on_change, settle_ms) if settle_ms > 0 else on_change)
    if inspector_service.inspector(ctx).holds(picker):
        inspector_service.inspector(ctx).popToRoot()       # refilled below, then shown again
    picker.setItems(items, checked)
    inspector_service.push(ctx, key, title, picker, tab=tab, anchor=anchor)
    return picker


def _flush(picker: MultiSelect) -> None:
    """Apply a pending settled change now."""
    entry = getattr(picker, _SETTLE, None)
    if entry is None:
        return
    timer, pending, apply = entry
    timer.stop()
    if pending:
        keys = pending.pop()
        pending.clear()
        apply(keys)


def _settled(ctx, picker: MultiSelect, on_change: Callable[[list], None], settle_ms: int):
    from PySide6.QtCore import QTimer

    old = getattr(picker, _SETTLE, None)
    if old is not None:
        old[0].stop()
        old[0].deleteLater()
    timer = QTimer(picker)
    timer.setSingleShot(True)
    timer.setInterval(int(settle_ms))
    pending: list = []
    setattr(picker, _SETTLE, (timer, pending, on_change))
    timer.timeout.connect(lambda p=picker: _flush(p))

    def _left(_key: str, p=picker) -> None:
        # The page was left with a change still waiting out its timer.
        if shiboken6.isValid(p) and not inspector_service.inspector(ctx).holds(p):
            _flush(p)

    insp = inspector_service.inspector(ctx)
    if not picker.property("settleWatched"):
        picker.setProperty("settleWatched", True)
        insp.pageChanged.connect(_left)

    def _on_change(keys: list) -> None:
        pending.clear()
        pending.append(list(keys))
        timer.start()

    return _on_change


# ── a colour ─────────────────────────────────────────────────────────────────

_COLOURS, _COLOUR_HANDLER = "_aw_colour_pages", "_aw_colour_handler"


def open_colour(ctx, anchor: QWidget, *, key: str, title: str, color,
                on_pick: Callable[[object], None], recents: Iterable = (),
                tab: str = "display") -> QWidget | None:
    """Show the colour page in the inspector, from the swatch *anchor*.

    *on_pick* is called with a ``QColor`` on every change — a swatch, the
    custom picker, a hex value. Pressing *anchor* again while its page is on
    show leaves the page (and ``None`` is returned). One page per anchor,
    re-seeded on every open."""
    from widgets.colour_page import ColourPage

    if anchor is None:
        return None
    mine = getattr(anchor, _COLOURS, None)
    if mine is None:
        mine = {}
        setattr(anchor, _COLOURS, mine)
    page = mine.get(key)
    if page is None or not shiboken6.isValid(page):
        page = ColourPage(anchor, recents=list(recents) or None)
        page.setObjectName(f"ColourPage:{key}")
        page.hide()
        page.colorPicked.connect(lambda c, p=page: getattr(p, _COLOUR_HANDLER)(c))
        mine[key] = page
    if inspector_service.is_showing(ctx, page):
        inspector_service.pop(ctx)
        return None
    setattr(page, _COLOUR_HANDLER, on_pick)
    page.setColor(color)
    inspector_service.push(ctx, key, title, page, tab=tab, anchor=anchor)
    return page


# ── a colour map ─────────────────────────────────────────────────────────────

def wire_lut_selector(ctx, selector, *, key: str, title: str = "Colour map", tab: str = "display") -> None:
    """Show *selector*'s searchable list of colour maps as a page of the
    inspector when its trigger is pressed, and leave the page when a map is
    picked. The selector (``widgets.lut_selector.LutSelector``) opens no
    surface of its own; this is the host it hands its list to."""

    def _show(content: QWidget) -> None:
        if inspector_service.is_showing(ctx, content):
            inspector_service.pop(ctx)
            return
        inspector_service.push(ctx, key, title, content, tab=tab, anchor=selector)

    def _picked() -> None:
        content = selector.listWidget()
        if inspector_service.is_showing(ctx, content):
            inspector_service.pop(ctx)

    selector.listRequested.connect(_show)
    selector.listPicked.connect(_picked)
