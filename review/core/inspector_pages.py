"""Pages of the inspector that every feature shows the same way.

A feature that lets the user pick several of a list — timepoints, series,
fields of view, gates — shows ``widgets.multi_select.MultiSelect`` as a page of
the window's inspector through :func:`open_multi_select`, and nothing else: no
menu of checkboxes, no dropdown of its own. The page applies as a box is
ticked, and the button that opened it goes on reading the choice.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable, Iterable

import shiboken6
from PySide6.QtWidgets import QWidget

from review.core import inspector_service
from widgets.multi_select import MultiSelect

#: picker -> the callable its ``changed`` reaches. The picker is built once per
#: anchor and refilled on every open, so the handler is swapped, not stacked.
_handlers: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


#: anchor -> {key: picker}. Not ``anchor.findChild``: while its page is on
#: show the picker is the inspector's child, not the anchor's, and a second
#: press would then build a second picker and stack a second page.
_pickers: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _picker_for(anchor: QWidget, key: str, empty_text: str) -> MultiSelect:
    mine = _pickers.setdefault(anchor, {})
    picker = mine.get(key)
    if picker is None or not shiboken6.isValid(picker):
        picker = MultiSelect(anchor, empty_text=empty_text)
        picker.setObjectName(f"MultiSelect:{key}")
        picker.hide()
        picker.changed.connect(lambda keys, p=picker: _handlers[p](keys))
        mine[key] = picker
    return picker


def open_multi_select(ctx, anchor: QWidget, *, key: str, title: str, items: Iterable,
                      checked: Iterable, on_change: Callable[[list], None],
                      tab: str = "data",
                      empty_text: str = "Nothing to choose from.") -> MultiSelect | None:
    """Show a multi-select as a page of the inspector, from the control *anchor*.

    *items* are ``(key, label)`` pairs or strings and *checked* the keys ticked
    now; *on_change* is called with the checked keys on every tick and once
    for All and for None. Pressing *anchor* again while its page is on show
    leaves the page (and ``None`` is returned). The picker is *anchor*'s
    child, refilled on every open, so it always shows the list as it is now."""
    if anchor is None:
        return None
    picker = _picker_for(anchor, key, empty_text)
    if inspector_service.is_showing(ctx, picker):
        inspector_service.pop(ctx)
        return None
    _handlers[picker] = on_change
    if inspector_service.inspector(ctx).holds(picker):
        inspector_service.inspector(ctx).popToRoot()       # refilled below, then shown again
    picker.setItems(items, checked)
    inspector_service.push(ctx, key, title, picker, tab=tab, anchor=anchor)
    return picker
