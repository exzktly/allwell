"""One panel for everything that changes what a page shows.

An :class:`Inspector` holds a row of **tabs**, and under each tab a **stack of
pages**. A tab's first page is the tab's own widget; a sub-option is a page
pushed on top of it, reached with one press and left with the back arrow. The
header is the same on every page: a back button (hidden on a tab's first page),
a one-line breadcrumb above the page's title, and a button reading **Close**.

The inspector builds none of what it shows. Every widget in it is **lent**: its
parent, its place in that parent's layout, its width limits and whether it was
hidden are read before it moves, and it goes back exactly there when its page is
popped, when its tab is given another widget, when :meth:`giveBackAll` is called
and when the inspector is destroyed. So the widget a page built — with every
connection it made, and every later ``setText`` / ``setVisible`` it does — is the
widget on screen, and nothing has to be rebuilt or copied.

Two things about lending are easy to get wrong, and both are handled here:

* **A place is an index among the widgets that were there**, not among the ones
  that happen to be there when the widget returns. Two widgets lent from one
  layout may come back in either order, so each record keeps the index the
  widget had *with every other lent sibling counted in*, and the insert position
  is that index less the siblings still out.
* **A lent widget may be destroyed by its owner** — a page that rebuilds its
  controls on a data change deletes them. The inspector pops a page whose widget
  is destroyed rather than keep a dead wrapper on its stack.

The inspector never closes itself. **Close** emits :attr:`closeRequested` and
its host — a column beside the page, a sheet over it — decides what closing is.
A page pushed with ``large=True`` asks the host for its full height through
:attr:`sizeWanted`.

Qt-only, no All-Well knowledge. It declares itself to
:mod:`widgets.form_registry` as kind ``"inspector"`` so a shell can find it
without recognising its class.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import shiboken6
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLayout, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from ui import theme
from widgets import form_registry
from widgets.segmented_control import SegmentedControl

#: Qt's QWIDGETSIZE_MAX — what lifts a width pin.
_QWIDGETSIZE_MAX = 16_777_215

#: The form kind an inspector declares (``widgets.form_registry``).
FORM_KIND = "inspector"

#: What the breadcrumb's parts are joined with.
CRUMB_SEPARATOR = " › "


@dataclass
class _Loan:
    """Where a lent widget came from, and what it looked like there."""

    widget: QWidget
    home: QWidget | None
    layout: QLayout | None
    #: The widget's index in ``layout`` with every other widget this inspector
    #: holds from the same layout counted as still present.
    index: int
    widths: tuple[int, int]
    hidden: bool
    children: list[tuple[QWidget, tuple[int, int]]] = field(default_factory=list)


@dataclass
class _Page:
    key: str
    title: str
    widget: QWidget
    slot: QWidget
    large: bool


def _alive(obj) -> bool:
    return obj is not None and shiboken6.isValid(obj)


def _widths(w: QWidget) -> tuple[int, int]:
    return w.minimumWidth(), w.maximumWidth()


class Inspector(QWidget):
    """Tabs, a stack of pages per tab, a header, and the lending of widgets."""

    #: **Close** was pressed. The host closes; the inspector does not.
    closeRequested = Signal()
    #: The page on show changed: the key of the page now on top, or the tab's
    #: key when the tab shows its first page.
    pageChanged = Signal(str)
    #: The tab on show changed (also emitted with ``pageChanged``).
    tabChanged = Signal(str)
    #: ``"full"`` when the page on show was pushed ``large``, else ``"half"``.
    sizeWanted = Signal(str)
    #: What the page on show holds laid out again (a control in it grew, a row
    #: was shown): a host that sizes itself to its content measures again.
    contentResized = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Inspector")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._tabs: list[tuple[str, str]] = []
        self._tab_widgets: dict[str, QWidget] = {}
        self._tab_slots: dict[str, QWidget] = {}
        self._tab_enabled: dict[str, bool] = {}
        self._pages: dict[str, list[_Page]] = {}
        self._loans: list[_Loan] = []
        self._current: str | None = None
        self._title = ""
        self._size = "half"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget(self)
        head.setObjectName("InspectorHeader")
        row = QHBoxLayout(head)
        s = theme.Spacing
        row.setContentsMargins(s.sm, s.sm, s.sm, s.sm)
        row.setSpacing(s.sm)
        self.back_button = QToolButton(head)
        self.back_button.setObjectName("InspectorBack")
        self.back_button.setText("‹")
        self.back_button.setToolTip("Back")
        self.back_button.setAccessibleName("Back")
        self.back_button.setAutoRaise(True)
        self.back_button.clicked.connect(lambda _=False: self.popPage())
        row.addWidget(self.back_button)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        self.crumb_label = QLabel(head)
        self.crumb_label.setObjectName("InspectorCrumb")
        self.crumb_label.setProperty("role", "muted")
        self.title_label = QLabel(head)
        self.title_label.setObjectName("InspectorTitle")
        self.title_label.setProperty("role", "heading")
        for lab in (self.crumb_label, self.title_label):
            # A long title gives way rather than holding the panel wide.
            lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lab.setTextFormat(Qt.PlainText)
            titles.addWidget(lab)
        row.addLayout(titles, 1)
        self.close_button = QPushButton("Close", head)
        self.close_button.setObjectName("InspectorClose")
        self.close_button.setProperty("variant", "secondary")
        self.close_button.clicked.connect(lambda _=False: self.closeRequested.emit())
        row.addWidget(self.close_button)
        outer.addWidget(head)
        self._head = head

        self._tab_row = QWidget(self)
        tab_lay = QHBoxLayout(self._tab_row)
        tab_lay.setContentsMargins(s.sm, 0, s.sm, s.sm)
        self._tab_bar: SegmentedControl | None = None
        outer.addWidget(self._tab_row)

        self._stack = QStackedWidget(self)
        self._empty = QWidget(self._stack)
        self._stack.addWidget(self._empty)
        outer.addWidget(self._stack, 1)

        self._sync_header()
        form_registry.declare(self, FORM_KIND)

    # ── tabs ─────────────────────────────────────────────────────────────
    def setTabs(self, tabs: list[tuple[str, str]]) -> None:
        """Name the tabs, ``[(key, title)]``, in the order they are shown.

        Every tab starts with no widget, and so disabled: a tab with nothing in
        it keeps its place, so the inspector has one shape on every page."""
        self.giveBackAll()
        for slot in self._tab_slots.values():
            self._stack.removeWidget(slot)
            slot.deleteLater()
        self._tabs = [(str(k), str(t)) for k, t in tabs]
        self._tab_widgets = {}
        self._tab_slots = {}
        self._tab_enabled = {k: True for k, _t in self._tabs}
        self._pages = {k: [] for k, _t in self._tabs}
        if self._tab_bar is not None:
            self._tab_row.layout().removeWidget(self._tab_bar)
            self._tab_bar.deleteLater()
        bar = SegmentedControl(self._tab_row)
        bar.setObjectName("InspectorTabs")
        for key, title in self._tabs:
            bar.addSegment(title, data=key)
        bar.currentChanged.connect(self._on_tab_picked)
        self._tab_row.layout().addWidget(bar)
        self._tab_bar = bar
        for key, _t in self._tabs:
            slot = self._new_slot()
            self._tab_slots[key] = slot
            self._stack.addWidget(slot)
        self._current = self._tabs[0][0] if self._tabs else None
        self._sync_tab_bar()
        self._show_top()

    def tabs(self) -> list[tuple[str, str]]:
        return list(self._tabs)

    def tabTitle(self, key: str) -> str:
        return dict(self._tabs).get(key, "")

    def setTabWidget(self, key: str, widget: QWidget | None, *,
                     free_children: bool = False) -> None:
        """Lend *widget* to tab *key* as its first page; ``None`` gives the
        tab's widget back and leaves the tab disabled."""
        if key not in self._tab_slots:
            raise KeyError(key)
        old = self._tab_widgets.get(key)
        if old is widget:
            return
        if old is not None:
            del self._tab_widgets[key]
            self._give_back(old)
        if widget is not None:
            self._lend(widget, self._tab_slots[key], free_children=free_children)
            self._tab_widgets[key] = widget
        self._sync_tab_bar()
        if key == self._current and not self._tab_usable(key) and not self._pages[key]:
            self._current = self._first_usable() or key
            self._show_top()

    def tabWidget(self, key: str) -> QWidget | None:
        return self._tab_widgets.get(key)

    def setTabEnabled(self, key: str, enabled: bool) -> None:
        if key not in self._tab_enabled:
            raise KeyError(key)
        self._tab_enabled[key] = bool(enabled)
        self._sync_tab_bar()

    def isTabEnabled(self, key: str) -> bool:
        return self._tab_usable(key)

    def showTab(self, key: str) -> bool:
        """Show tab *key* at the page it was left on. ``False``, and nothing
        changes, for a tab that is disabled or has no widget."""
        if not self._tab_usable(key) and not self._pages.get(key):
            return False
        changed = key != self._current
        self._current = key
        self._show_top()
        if changed:
            self.tabChanged.emit(key)
        return True

    def currentTab(self) -> str | None:
        return self._current

    # ── pages ────────────────────────────────────────────────────────────
    def pushPage(self, key: str, title: str, widget: QWidget, *,
                 tab: str | None = None, large: bool = False,
                 free_children: bool = False) -> None:
        """Lend *widget* and show it as a page on top of *tab* (the tab on show
        when ``None``). Pushing a key that is already on that tab's stack pops
        back to it instead of stacking a second copy."""
        tab = tab or self._current
        if tab is None or tab not in self._pages:
            raise KeyError(tab)
        stack = self._pages[tab]
        for i, page in enumerate(stack):
            if page.key == key and page.widget is widget:
                for extra in reversed(stack[i + 1:]):
                    self._drop(tab, extra)
                self._current = tab
                self._show_top()
                return
        slot = self._new_slot()
        self._stack.addWidget(slot)
        self._lend(widget, slot, free_children=free_children)
        stack.append(_Page(str(key), str(title), widget, slot, bool(large)))
        changed = tab != self._current
        self._current = tab
        self._show_top()
        if changed:
            self.tabChanged.emit(tab)

    def popPage(self) -> bool:
        """Leave the page on show for the one under it. ``False`` on a tab's
        first page, which has nothing under it."""
        stack = self._pages.get(self._current or "", [])
        if not stack:
            return False
        self._drop(self._current, stack[-1])
        if not stack and not self._tab_usable(self._current or ""):
            # A page pushed on a tab that has no first page of its own (the
            # desk's Data tab, whose first page is the band above the plot):
            # there is nothing under it, so the inspector falls back to a tab
            # it can show, and with none asks its host to close.
            fallback = self._first_usable()
            if fallback is None:
                self._show_top()
                self.closeRequested.emit()
                return True
            self._current = fallback
            self._show_top()
            self.tabChanged.emit(fallback)
            return True
        self._show_top()
        return True

    def popToRoot(self, tab: str | None = None) -> None:
        """Give back every page pushed on *tab* (every tab when ``None``)."""
        for key in ([tab] if tab else list(self._pages)):
            for page in reversed(list(self._pages.get(key, []))):
                self._drop(key, page)
        self._show_top()

    def depth(self) -> int:
        """How many pages are pushed on the tab on show: 0 on its first page."""
        return len(self._pages.get(self._current or "", []))

    def currentKey(self) -> str:
        """The key of the page on show, or the tab's key on its first page."""
        stack = self._pages.get(self._current or "", [])
        return stack[-1].key if stack else (self._current or "")

    def currentWidget(self) -> QWidget | None:
        stack = self._pages.get(self._current or "", [])
        return stack[-1].widget if stack else self._tab_widgets.get(self._current or "")

    def holds(self, widget: QWidget) -> bool:
        """Whether *widget* is lent to this inspector now."""
        return any(loan.widget is widget for loan in self._loans)

    def pageKeys(self, tab: str | None = None) -> list[str]:
        return [p.key for p in self._pages.get(tab or self._current or "", [])]

    def setTitle(self, text: str) -> None:
        """What the header reads on a tab's first page: the page the inspector
        serves ("Bar Plots")."""
        self._title = str(text or "")
        self._sync_header()

    def title(self) -> str:
        return self._title

    def wantedSize(self) -> str:
        return self._size

    # ── giving back ──────────────────────────────────────────────────────
    def giveBackAll(self) -> None:
        """Every lent widget — every page of every tab, and each tab's own —
        goes back to where it came from."""
        for key in list(self._pages):
            for page in reversed(list(self._pages[key])):
                self._drop(key, page)
        for key in list(self._tab_widgets):
            widget = self._tab_widgets.pop(key)
            self._give_back(widget)
        self._sync_tab_bar()
        self._show_top()

    def event(self, ev) -> bool:  # Qt naming
        # Deleted with widgets still lent: they would be destroyed with their
        # slots. ``DeferredDelete`` is the last moment the wrapper is whole.
        if ev.type() == QEvent.DeferredDelete:
            self.giveBackAll()
        return super().event(ev)

    # ── internals ────────────────────────────────────────────────────────
    def _new_slot(self) -> QWidget:
        """A scrolling holder for one lent widget, which sits at its top."""
        scroll = QScrollArea(self._stack)
        scroll.setObjectName("InspectorPage")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setObjectName("InspectorPageBody")
        lay = QVBoxLayout(holder)
        s = theme.Spacing
        lay.setContentsMargins(s.sm, s.sm, s.sm, s.sm)
        lay.addStretch(1)
        scroll.setWidget(holder)
        holder.installEventFilter(self)
        return scroll

    def eventFilter(self, obj, ev) -> bool:  # Qt naming
        if ev.type() in (QEvent.LayoutRequest, QEvent.Resize):
            self.contentResized.emit()
        return False

    def tabSlot(self, key: str) -> QScrollArea | None:
        """The scroll area tab *key*'s own widget sits in."""
        return self._tab_slots.get(key)

    def currentSlot(self) -> QScrollArea | None:
        stack = self._pages.get(self._current or "", [])
        return stack[-1].slot if stack else self._tab_slots.get(self._current or "")

    def contentHeight(self, width: int) -> int:
        """How tall the inspector is with the page on show at its full height,
        *width* pixels wide: the header, the tab row while it shows, and what
        the page holds."""
        total = self._head.sizeHint().height()
        if not self._tab_row.isHidden():
            total += self._tab_row.sizeHint().height()
        slot = self.currentSlot()
        holder = slot.widget() if slot is not None and _alive(slot) else None
        lay = holder.layout() if holder is not None else None
        if lay is not None:
            total += (lay.totalHeightForWidth(width) if lay.hasHeightForWidth()
                      else lay.totalSizeHint().height())
        return total

    def slotOf(self, widget: QWidget) -> QScrollArea | None:
        """The scroll area a lent widget sits in (a host fits its content)."""
        for slot in (*self._tab_slots.values(),
                     *(p.slot for st in self._pages.values() for p in st)):
            if _alive(slot) and slot.widget() is not None and widget.parentWidget() is slot.widget():
                return slot
        return None

    def _lend(self, widget: QWidget, slot: QScrollArea, *, free_children: bool) -> None:
        home = widget.parentWidget()
        layout = home.layout() if home is not None else None
        index = layout.indexOf(widget) if layout is not None else -1
        if index >= 0:
            # Counted as if every sibling this inspector already holds from the
            # same layout were still in it.
            for other in sorted((o for o in self._loans if o.layout is layout and o.index >= 0),
                                key=lambda o: o.index):
                if other.index <= index:
                    index += 1
        else:
            layout = None
        children = ([(c, _widths(c)) for c in widget.findChildren(QWidget)
                     if c.parentWidget() is widget] if free_children else [])
        loan = _Loan(widget, home, layout, index, _widths(widget), widget.isHidden(), children)
        self._loans.append(loan)
        slot.widget().layout().insertWidget(0, widget)
        for w in (widget, *(c for c, _ in children)):
            w.setMinimumWidth(0)
            w.setMaximumWidth(_QWIDGETSIZE_MAX)
        widget.show()
        widget.destroyed.connect(self._on_lent_destroyed)

    def _give_back(self, widget: QWidget) -> None:
        loan = next((o for o in self._loans if o.widget is widget), None)
        if loan is None:
            return
        self._loans.remove(loan)
        if not _alive(widget):
            return
        try:
            widget.destroyed.disconnect(self._on_lent_destroyed)
        except (RuntimeError, TypeError):
            pass  # never connected, or already gone: nothing to undo
        try:
            lo, hi = loan.widths
            widget.setMinimumWidth(lo)
            widget.setMaximumWidth(hi)
            for child, (clo, chi) in loan.children:
                if _alive(child):
                    child.setMinimumWidth(clo)
                    child.setMaximumWidth(chi)
            if _alive(loan.layout) and _alive(loan.home):
                still_out = sum(1 for o in self._loans
                                if o.layout is loan.layout and 0 <= o.index < loan.index)
                loan.layout.insertWidget(max(0, loan.index - still_out), widget)
            elif _alive(loan.home):
                widget.setParent(loan.home)
            else:
                widget.setParent(None)
            widget.setVisible(not loan.hidden and loan.layout is not None)
        except RuntimeError:
            pass  # the owner went while its widget was out

    def _drop(self, tab: str, page: _Page) -> None:
        stack = self._pages.get(tab, [])
        if page in stack:
            stack.remove(page)
        self._give_back(page.widget)
        if _alive(page.slot):
            self._stack.removeWidget(page.slot)
            page.slot.deleteLater()

    def _on_lent_destroyed(self, obj=None) -> None:
        """A lent widget's owner deleted it: forget it, and leave its page."""
        dead = [o for o in self._loans if not _alive(o.widget) or o.widget is obj]
        if not dead:
            return
        gone = {id(o.widget) for o in dead}
        self._loans = [o for o in self._loans if o not in dead]
        for key in [k for k, w in self._tab_widgets.items() if id(w) in gone]:
            del self._tab_widgets[key]
        for tab, stack in self._pages.items():
            for page in [p for p in stack if id(p.widget) in gone]:
                stack.remove(page)
                if _alive(page.slot):
                    self._stack.removeWidget(page.slot)
                    page.slot.deleteLater()
        if _alive(self):
            self._sync_tab_bar()
            self._show_top()

    def _tab_usable(self, key: str) -> bool:
        return bool(self._tab_enabled.get(key)) and key in self._tab_widgets

    def _first_usable(self) -> str | None:
        return next((k for k, _t in self._tabs if self._tab_usable(k)), None)

    def _on_tab_picked(self, index: int) -> None:
        if not (0 <= index < len(self._tabs)):
            return
        key = self._tabs[index][0]
        if key != self._current and not self.showTab(key):
            self._sync_tab_bar()       # a disabled tab: put the highlight back

    def _sync_tab_bar(self) -> None:
        bar = self._tab_bar
        if bar is None or not _alive(bar):
            return
        for i, (key, _t) in enumerate(self._tabs):
            bar.setSegmentEnabled(i, self._tab_usable(key))
        if self._current is not None:
            was = bar.blockSignals(True)
            bar.setCurrentByData(self._current)
            bar.blockSignals(was)

    def _show_top(self) -> None:
        tab = self._current
        stack = self._pages.get(tab or "", [])
        if stack:
            self._stack.setCurrentWidget(stack[-1].slot)
        elif tab in self._tab_slots:
            self._stack.setCurrentWidget(self._tab_slots[tab])
        else:
            self._stack.setCurrentWidget(self._empty)
        self._sync_tab_bar()
        self._sync_header()
        size = "full" if stack and stack[-1].large else "half"
        self._size = size
        self.sizeWanted.emit(size)
        self.pageChanged.emit(self.currentKey())

    def _sync_header(self) -> None:
        stack = self._pages.get(self._current or "", [])
        on_page = bool(stack)
        self.back_button.setVisible(on_page)
        self._tab_row.setVisible(not on_page and bool(self._tabs))
        if on_page:
            parts = [self.tabTitle(self._current or ""), *(p.title for p in stack[:-1])]
            self.crumb_label.setText(CRUMB_SEPARATOR.join(p for p in parts if p))
            self.title_label.setText(stack[-1].title)
        else:
            self.crumb_label.setText("")
            self.title_label.setText(self._title)
        self.crumb_label.setVisible(bool(self.crumb_label.text()))
