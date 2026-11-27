"""A page's panels of live options, shown as pages of the inspector.

A page used to keep its less-used options in a *popover*: a button that dropped
a floating panel over the plot. A popover covers what it adjusts, closes when
anything else is clicked, and — in phone layout, where the button already sat
inside the Controls sheet — opened one surface on top of another.

:func:`make_popover_button` keeps the shape every page builds against — it
returns ``(button, content, content_layout)`` and the page fills
``content_layout`` with live controls — and changes what the button *does*:
pressing it shows ``content`` in the window's one inspector
(``review.core.inspector_service``). A panel is one of three kinds:

* **the root of a tab** (``root=True``): the page's one panel of display
  options *is* the inspector's Display tab. Its button reads **Display** and is
  one of the band's three fixed buttons; pressing it opens the inspector on
  that tab, and pressing it again closes it.
* **a Data panel** (``tab="data"``): a sub-option of what is plotted — Fold
  change, Fit, Colour & split. Its button stays in the band and reads its
  current value, ``Fold change: vs DMSO ›``; pressing it shows the panel as a
  page, and pressing it again leaves the page.
* **a Display panel** that is not the root (``tab="display"``): a row on the
  Display tab — Brightness, Colours — which the page adds to its root panel's
  layout rather than to the band.

``content`` is built hidden, parented to its button and in no layout; the
inspector borrows it and gives it back there.

What a panel knows about itself is on the button as Qt properties
(``widgets.control_band_form.PANEL_PROPERTY``), so the band's form can lay a
panel out as a row without importing this module; everything else is in a
weakly-keyed record here.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSizePolicy, QToolButton, QVBoxLayout, QWidget

from core.safe import swallowed
from review.core import inspector_service
from widgets.control_band import ControlBand
from widgets.control_band_form import PANEL_PROPERTY, PANEL_ROOT, PANEL_ROW, ROLE_PROPERTY

#: What ends a button that opens a page. Not a menu arrow: the button has no
#: menu, and Qt draws no indicator on it (CLAUDE.md §7.12).
CHEVRON = "›"


@dataclass
class Panel:
    """One panel: its button, its content, and where it goes."""

    content: QWidget
    tab: str
    root: bool
    title: str
    summary: Callable[[], str] | None
    large: bool
    ctx: Any
    #: Called just before the panel is shown, in order: a panel whose options
    #: depend on what was defined since it was built refreshes itself here.
    on_open: list[Callable[[], None]] = field(default_factory=list)
    #: Called when the panel stops being shown — its page was left, the
    #: inspector closed or the page changed: a panel that puts off an
    #: expensive update while it is being edited runs it here.
    on_close: list[Callable[[], None]] = field(default_factory=list)
    shown: bool = False


#: Every live panel button. A set of weak references and nothing else: a
#: panel's record names its context, and the context reaches its buttons
#: through the widget registry, so a module-level *mapping* from button to
#: record is a chain from this module to the window that no closed window ever
#: breaks (measured: three ``test_a_closed_review_app_is_freed`` failures,
#: about 31 MB a window). The record lives on the button instead, where it is
#: an ordinary cycle the collector frees with the window.
_buttons: weakref.WeakSet = weakref.WeakSet()
#: Windows whose inspector already re-reads the panels' values on a page change.
_watched: weakref.WeakSet = weakref.WeakSet()

#: The attribute a button carries its :class:`Panel` in.
_RECORD = "_aw_inspector_panel"


def _items() -> list:
    """``(button, panel)`` for every live panel button."""
    out = []
    for button in list(_buttons):
        if shiboken6.isValid(button):
            panel = getattr(button, _RECORD, None)
            if panel is not None:
                out.append((button, panel))
    return out


def clean_title(text: str) -> str:
    """``"Fold change"`` for the ``"Fold change ▾"`` a call site still writes."""
    return str(text).replace("▾", "").replace(CHEVRON, "").strip()


def make_popover_button(text: str, parent: QWidget, *, ctx=None, tab: str = "display",
                        root: bool = False, summary: Callable[[], str] | None = None,
                        large: bool = False):
    """A band button whose panel is a page of the inspector.

    Returns ``(button, content_widget, content_layout)`` — add controls to
    ``content_layout``. *tab* is the inspector tab the panel belongs to;
    *root* makes it that tab's first page; *summary* returns the panel's
    current value, which the button shows; *large* asks for the inspector's
    full height (an editor)."""
    title = clean_title(text)
    btn = QToolButton(parent)
    btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
    btn.setProperty("variant", "secondary")
    btn.setProperty(PANEL_PROPERTY, PANEL_ROOT if root else PANEL_ROW)
    btn.setProperty(ROLE_PROPERTY, "display" if root else "panel")
    content = QWidget(btn)
    content.setObjectName("InspectorPanel")
    content.hide()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(6)
    setattr(btn, _RECORD, Panel(content, tab, bool(root), title, summary, bool(large), ctx))
    _buttons.add(btn)
    refresh(btn)
    btn.clicked.connect(lambda _=False, b=btn: press(b))
    return btn, content, content_layout


def panel_of(button: QWidget) -> Panel | None:
    return getattr(button, _RECORD, None)


def on_open(button: QWidget, fn: Callable[[], None]) -> None:
    """Call *fn* each time *button*'s panel is about to be shown."""
    panel = panel_of(button)
    if panel is not None:
        panel.on_open.append(fn)


def on_close(button: QWidget, fn: Callable[[], None]) -> None:
    """Call *fn* each time *button*'s panel stops being shown."""
    panel = panel_of(button)
    if panel is not None:
        panel.on_close.append(fn)


def set_summary(button: QWidget, summary: Callable[[], str] | None) -> None:
    """Give *button* the callable its value is read from, after it was built
    (the state it reads often does not exist until the panel is filled)."""
    panel = panel_of(button)
    if panel is not None:
        panel.summary = summary
        refresh(button)


def as_display_row(button: QWidget) -> QWidget:
    """Shape *button* as a row of a Display page: it fills the page's width
    and reads from the left. The page adds it to its root panel's layout."""
    button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    button.setProperty("inspectorRow", True)
    return button


def button_text(panel: Panel) -> str:
    """What a panel's button reads: ``Display`` for a tab's root, and for a
    page ``Title: value ›`` — or ``Title ›`` when it has no one value."""
    if panel.root:
        return panel.title
    value = ""
    if panel.summary is not None:
        try:
            value = str(panel.summary() or "")
        except (RuntimeError, KeyError, AttributeError):
            swallowed()  # the state it reads is not built yet, or is being torn down
    return f"{panel.title}: {value} {CHEVRON}" if value else f"{panel.title} {CHEVRON}"


def refresh(button: QWidget) -> None:
    """Re-read *button*'s value into its text."""
    panel = panel_of(button)
    if panel is None or not shiboken6.isValid(button):
        return
    text = button_text(panel)
    if button.text() != text:
        button.setText(text)


def refresh_all(ctx=None) -> None:
    """Re-read every live panel button's value (those of *ctx* when given)."""
    for button, panel in _items():
        if ctx is None or _ctx_of(button, panel) is ctx:
            refresh(button)


def _ctx_of(button: QWidget, panel: Panel) -> Any | None:
    if panel.ctx is not None:
        return panel.ctx
    # Built without a context (a reusable builder): the window that holds the
    # button knows its own.
    node = button
    while node is not None:
        ctx = getattr(node, "_ctx", None)
        if ctx is not None:
            panel.ctx = ctx
            return ctx
        node = node.parentWidget()
    return None


def _watch(ctx) -> None:
    """Once per window: a page left is a value possibly changed, so every
    panel button re-reads its value when the inspector's page changes."""
    win = ctx.window
    if win in _watched:
        return
    _watched.add(win)
    inspector_service.inspector(ctx).pageChanged.connect(lambda _key: _on_page_changed(ctx))


def _on_page_changed(ctx) -> None:
    insp = inspector_service.inspector(ctx)
    for button, panel in _items():
        if panel.shown and not insp.holds(panel.content):
            panel.shown = False
            for fn in list(panel.on_close):
                try:
                    fn()
                except RuntimeError:
                    swallowed()  # what it would have updated went with its page
    refresh_all(ctx)


def press(button: QWidget) -> bool:
    """What pressing a panel's button does. Returns whether its panel is on
    show afterwards."""
    panel = panel_of(button)
    if panel is None:
        return False
    ctx = _ctx_of(button, panel)
    if ctx is None:
        return False
    _watch(ctx)
    if panel.root:
        _run_on_open(panel)
        return inspector_service.toggle_tab(ctx, panel.tab, anchor=button)
    if inspector_service.is_showing(ctx, panel.content):
        inspector_service.pop(ctx)                  # the button is a toggle
        return False
    _run_on_open(panel)
    panel.shown = inspector_service.push(ctx, panel.title, panel.title, panel.content,
                                         tab=panel.tab, large=panel.large, anchor=button)
    return panel.shown


def _run_on_open(panel: Panel) -> None:
    for fn in list(panel.on_open):
        try:
            fn()
        except RuntimeError:
            swallowed()  # the row it refreshes went with its page


# ── which page's panel is the root of a tab ──────────────────────────────────

def _band_of(button: QWidget) -> QWidget | None:
    node = button.parentWidget()
    while node is not None:
        if isinstance(node, ControlBand):
            return node
        node = node.parentWidget()
    return None


def _is_current(ctx, button: QWidget) -> bool:
    """Whether *button* belongs to the page on screen: not hidden by its page
    (a band shared by several leaves hides the other leaves' controls), and in
    a band — or, with no band, a place — that is showing, or that the
    inspector is holding as the Data tab."""
    if not shiboken6.isValid(button) or button.isHidden():
        return False
    host = _band_of(button) or button.parentWidget()
    if host is None:
        return False
    if ctx.runtime.split.active:
        from review.core import split_view
        pane = split_view._resolve_focused_pane(ctx, host)
        if pane and pane != ctx.runtime.split.focused:
            return False
    if host.isVisibleTo(ctx.window):
        return True
    return inspector_service.inspector(ctx).holds(host)


def find_roots(ctx, _leaf: str) -> dict:
    """``{tab: content}`` for the root panels of the page on screen."""
    found: dict = {}
    for button, panel in _items():
        if not panel.root or panel.tab in found:
            continue
        if _ctx_of(button, panel) is not ctx or not _is_current(ctx, button):
            continue
        _run_on_open(panel)
        panel.shown = True
        found[panel.tab] = panel.content
    return found


inspector_service.add_root_finder(find_roots)
