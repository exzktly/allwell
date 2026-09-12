"""`NamedPageStack` — a `QStackedWidget` addressed by page name rather than index.

Every pane of this application is a stack of named pages: Review's centre area,
the Datasets tool pages, Analyze's own panels. Addressing them by index means
every insertion renumbers every caller, so this stack keeps a name per page and
exposes `addPage` / `setCurrentByName` / `currentName` / `pageNames` / `nameOf`,
plus detach and reattach for the split view.

It lives in `widgets/` because it knows nothing about All-Well: it takes widgets
and strings and returns widgets and strings. It sat inside the Review pane's
`centre_view` — a 846-line module that imports fifteen of Review's own modules —
while Datasets imported it across a package boundary, which made a general
widget look like one pane's internal and dragged a reader through that module to
find it.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QStackedWidget, QWidget

class NamedPageStack(QStackedWidget):
    """``QStackedWidget`` with name-keyed page lookup.

    Phase 15 replacement for the legacy ``_notebook`` / ``_plotting_notebook``
    ``QTabWidget``s. Exposes the v2 name-based API
    (``addPage`` / ``setCurrentByName`` / ``currentName`` / ``pageNames`` /
    ``nameOf``).
    """

    currentNameChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # ``_names`` is rebuilt to track the live stack order; ``_by_name`` holds
        # only currently-attached pages; ``_page_order`` is the canonical add
        # order (kept across detach/reattach so Split View can restore it).
        self._names: list[str] = []
        self._by_name: dict[str, QWidget] = {}
        self._page_order: list[str] = []
        super().currentChanged.connect(self._emit_name)

    def _rebuild_names(self) -> None:
        """Resync ``_names`` to the live stack order (after add/detach/reattach)."""
        names = []
        for i in range(self.count()):
            w = self.widget(i)
            names.append(next((n for n, wid in self._by_name.items() if wid is w), ""))
        self._names = names

    def addPage(self, name: str, widget: QWidget) -> int:
        idx = self.addWidget(widget)
        self._by_name[name] = widget
        if name not in self._page_order:
            self._page_order.append(name)
        self._rebuild_names()
        return idx

    def detachPage(self, name: str) -> QWidget | None:
        """Remove a page from the stack and return its widget (kept alive).

        Used by Split View to move a page into the side-by-side splitter. The
        canonical add order is preserved in ``_page_order`` so ``reattachPage``
        can restore the original position.
        """
        w = self._by_name.pop(name, None)
        if w is None:
            return None
        self.removeWidget(w)
        w.setParent(None)
        self._rebuild_names()
        return w

    def reattachPage(self, name: str, widget: QWidget) -> None:
        """Re-insert a previously detached page at its original relative slot."""
        insert_at = 0
        for nm in self._page_order:
            if nm == name:
                break
            if nm in self._by_name:
                insert_at += 1
        self.insertWidget(insert_at, widget)
        self._by_name[name] = widget
        if name not in self._page_order:
            self._page_order.append(name)
        self._rebuild_names()

    def setCurrentByName(self, name: str) -> bool:
        w = self._by_name.get(name)
        if w is None:
            return False
        self.setCurrentWidget(w)
        return True

    def currentName(self) -> str:
        idx = self.currentIndex()
        if 0 <= idx < len(self._names):
            return self._names[idx]
        return ""

    def pageNames(self) -> list[str]:
        return list(self._names)

    def nameOf(self, w: QWidget) -> str | None:
        idx = self.indexOf(w)
        if 0 <= idx < len(self._names):
            return self._names[idx]
        return None

    def _emit_name(self, idx: int) -> None:
        if 0 <= idx < len(self._names):
            self.currentNameChanged.emit(self._names[idx])
