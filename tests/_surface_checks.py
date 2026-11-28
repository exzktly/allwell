"""How many surfaces are open at once, measured on real widgets.

The rule (ARCHITECTURE.md, the inspector and window rules): while an inspector page, a
menu or a window is open, nothing it contains may open a second transient
surface, except an alert or the operating system's file dialog. So the number
of transient surfaces open at any moment — :func:`depth` — is never more than
one.

:func:`open_transients` is the definition of "transient surface": a ``QMenu``;
a widget with the ``Qt.Popup`` flag (``widgets.popover.Popover``, a combo box's
list); a visible ``QDialog`` or window other than the main one; an open
``widgets.drawer.Drawer`` (phone layout's sheets — an overlay). The desk's
docked inspector is **not** one: it is a pane of the page's own splitter, laid
out beside the page, and it is where a transient is replaced by a page.

:func:`crawl` presses every enabled button under a root and reports the depth
each press reached, and presses every button inside whatever a press opened.
It does not open a menu's submenus: under the offscreen platform a second
popup is shown or not depending on timing, which made that probe pass and fail
on the same tree, and a submenu is held from the syntax tree instead
(``tests/test_a_menu_holds_commands_only.py``). It never blocks: :func:`stub_blocking` turns every modal
call into its non-blocking form first (CLAUDE.md §7.10 #6), and a button that
drops a menu has its menu popped up rather than executed.
"""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QDialog, QFileDialog, QInputDialog, QMenu,
    QMessageBox, QPushButton, QToolButton, QWidget,
)


def _is_alert(w: QWidget) -> bool:
    return isinstance(w, (QMessageBox, QFileDialog))


def _belongs(w: QWidget, main: QWidget) -> bool:
    """Whether top-level *w* was opened from *main*: its parent chain (a
    window keeps its parent) leads there."""
    node = w.parentWidget()
    while node is not None:
        if node is main:
            return True
        node = node.parentWidget()
    return False


def open_transients(main: QWidget | None = None) -> list[QWidget]:
    """Every transient surface on screen now, alerts and file dialogs left out."""
    from widgets.drawer import Drawer
    found: list[QWidget] = []
    for w in QApplication.topLevelWidgets():
        if not shiboken6.isValid(w) or w is main or not w.isVisible() or _is_alert(w):
            continue
        if main is not None and not _belongs(w, main):
            # Another test's window, still waiting on its deferred delete in
            # this worker: not something this window opened.
            continue
        flags = w.windowFlags()
        is_popup = (flags & Qt.WindowType_Mask) == Qt.Popup
        is_tip = (flags & Qt.WindowType_Mask) == Qt.ToolTip
        if is_tip:
            continue
        if isinstance(w, (QMenu, QDialog)) or is_popup or w.isWindow():
            found.append(w)
    roots = [main] if main is not None else [
        w for w in QApplication.topLevelWidgets() if shiboken6.isValid(w)]
    for root in roots:
        for d in root.findChildren(Drawer):
            if d.isOpen() and d.isVisible():
                found.append(d)
    return found


def depth(main: QWidget | None = None) -> int:
    return len(open_transients(main))


def describe(widgets: list[QWidget]) -> str:
    return ", ".join(f"{type(w).__name__}({w.objectName() or w.windowTitle() or ''})"
                     for w in widgets)


def close_transients(main: QWidget | None = None) -> None:
    """Close what a press opened, innermost first, so the next press starts
    from the page as it was."""
    from widgets.drawer import Drawer
    for w in reversed(open_transients(main)):
        if not shiboken6.isValid(w):
            continue
        if isinstance(w, Drawer):
            w.closeNow()
        else:
            w.close()
    QApplication.processEvents()


def stub_blocking(monkeypatch) -> None:
    """Every call that would block the event loop returns at once instead:
    a dialog is shown, a menu popped up, an alert answered with its default."""
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: (self.show(), 0)[1])
    monkeypatch.setattr(QDialog, "open", lambda self, *a, **k: self.show())
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: self.popup(
        a[0] if a and isinstance(a[0], QPoint) else QPoint(10, 10)), raising=False)
    for name in ("information", "warning", "critical", "question", "about"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
    monkeypatch.setattr(QMessageBox, "exec", lambda self, *a, **k: QMessageBox.Cancel)
    for name in ("getOpenFileName", "getSaveFileName"):
        monkeypatch.setattr(QFileDialog, name, staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([], "")))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))
    monkeypatch.setattr(QFileDialog, "exec", lambda self, *a, **k: 0)
    for name in ("getText", "getItem", "getInt", "getDouble", "getMultiLineText"):
        monkeypatch.setattr(QInputDialog, name, staticmethod(lambda *a, **k: ("", False)))
    from widgets import menu_exec
    monkeypatch.setattr(menu_exec, "exec_menu",
                        lambda menu, pos: menu.popup(pos), raising=False)


def pressable(root: QWidget) -> list[QAbstractButton]:
    """The enabled push and tool buttons shown under *root*, collected by type
    (never ``findChildren(QWidget)``, which wraps every combo's popup
    container — CLAUDE.md §7.10 #6)."""
    out: list[QAbstractButton] = []
    for cls in (QPushButton, QToolButton):
        for b in root.findChildren(cls):
            if shiboken6.isValid(b) and b.isEnabled() and b.isVisibleTo(root):
                out.append(b)
    return out


def label_of(b: QAbstractButton) -> str:
    return (b.text() or b.toolTip() or b.objectName() or type(b).__name__).split("\n")[0][:60]


def press(b: QAbstractButton) -> None:
    """Press *b* as a user would, without blocking: a button that drops a menu
    has the menu popped up (``click`` would run it modally)."""
    menu = b.menu() if isinstance(b, (QToolButton, QPushButton)) else None
    if menu is not None:
        menu.aboutToShow.emit()
        menu.popup(b.mapToGlobal(b.rect().bottomLeft()))
    else:
        b.click()
    QApplication.processEvents()


class Crawl(list):
    """The presses that left more than one transient open, as ``(label, depth,
    what was open)`` — and, in :attr:`pressed`, every label that was pressed,
    so a crawl that found no buttons cannot pass for one that found no fault."""

    def __init__(self) -> None:
        super().__init__()
        self.pressed: list[str] = []


def crawl(root: QWidget, main: QWidget | None = None, *,
          skip=lambda _label: False) -> Crawl:
    """Press every enabled button under *root* once, and every button inside
    whatever that opened. What a press opens is closed again before the next,
    and a button the page removed meanwhile is passed over."""
    deep = Crawl()
    seen: set[int] = set()
    for _round in range(3):                     # a press may reveal more buttons
        fresh = [b for b in pressable(root) if id(b) not in seen]
        if not fresh:
            break
        for b in fresh:
            seen.add(id(b))
            if not shiboken6.isValid(b) or not b.isEnabled() or not b.isVisibleTo(root):
                continue
            label = label_of(b)
            if skip(label):
                continue
            press(b)
            deep.pressed.append(label)
            now = open_transients(main)
            if len(now) > 1:
                deep.append((label, len(now), describe(now)))
            # A second press on something the first opened: every button inside
            # a transient is pressed too, which is where depth two comes from.
            for t in list(now):
                if not shiboken6.isValid(t):
                    continue
                for inner in pressable(t):
                    if id(inner) in seen:
                        continue
                    seen.add(id(inner))
                    press(inner)
                    deep.pressed.append(f"{label} ▸ {label_of(inner)}")
                    stacked = open_transients(main)
                    if len(stacked) > 1:
                        deep.append((f"{label} ▸ {label_of(inner)}", len(stacked),
                                     describe(stacked)))
                    for extra in stacked:
                        if extra not in now and shiboken6.isValid(extra):
                            extra.close()
                    QApplication.processEvents()
            close_transients(main)
    return deep
