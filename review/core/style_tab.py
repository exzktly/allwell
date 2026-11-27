"""The Style tab: which figure's export-style panel the inspector shows.

Every plot page's band has a **Style** button (``ui_helpers.make_properties_button``),
built with the key of the figure it styles. Pressing it opens the inspector on
its Style tab with that figure's panel; pressing it again closes it. The panel
is the feature layer's (``review/features/export``), which core may not import,
so it is asked of the feature registry (the ``export_style.sidebar`` service).

The tab also has to be filled when no Style button was pressed: when the page
changes while the inspector is open, and when phone layout's Controls sheet
opens with Style as the tab beside Data. :func:`find_roots` is the inspector
service's root finder for that — it asks which Style button belongs to the
page on screen, by the same rule that finds a page's Display panel
(``inspector_panels._is_current``), and returns that figure's panel.
"""

from __future__ import annotations

import weakref

from PySide6.QtWidgets import QWidget

from core.safe import swallowed
from review.core import inspector_panels, inspector_service

TAB = "style"

#: Style button -> ``(ctx, figure key)``.
_buttons: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def register(button: QWidget, ctx, figure_key: str) -> None:
    _buttons[button] = (ctx, figure_key)


def figure_key_of(button: QWidget) -> str | None:
    entry = _buttons.get(button)
    return entry[1] if entry else None


def panel_for(ctx, figure_key: str) -> QWidget | None:
    """The style panel of *figure_key*'s figure, or ``None`` before the figure
    is drawn (or in a build without the export feature)."""
    try:
        return ctx.features.query("export_style.sidebar", ctx.window, figure_key, default=None)
    except RuntimeError:
        swallowed()  # the figure's page was torn down while it was asked
        return None


def press(button: QWidget) -> bool:
    """The Style button: open the inspector on this figure's panel, or close
    it when that is what it already shows. Returns whether it is open."""
    entry = _buttons.get(button)
    if entry is None:
        return False
    ctx, key = entry
    panel = panel_for(ctx, key)
    if panel is None:
        return False
    if inspector_service.shows_root(ctx, TAB, panel):
        inspector_service.close(ctx)
        return False
    return inspector_service.open_tab_with(ctx, TAB, panel, fill=True, anchor=button)


def find_roots(ctx, _leaf: str) -> dict:
    """``{"style": panel}`` for the page on screen, when it has a figure to style."""
    for button, (owner, key) in list(_buttons.items()):
        if owner is not ctx or not button.isEnabled():
            continue
        if inspector_panels._is_current(ctx, button):
            panel = panel_for(ctx, key)
            return {TAB: panel} if panel is not None else {}
    return {}


inspector_service.add_root_finder(find_roots)
