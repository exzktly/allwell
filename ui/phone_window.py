"""Phone layout: a window of its own — a dialog, a pop-out — fitted to the phone.

The main window fits the phone because phone layout maximizes it and rearranges
it. Every other window the app opens — Manage Remote Servers, Remote Storage,
the Storage dialog, the nanowell crops pop-out, Test detection, the editors and
curves Review opens beside a plot — was laid out for the desk and came up at
its desk size: wider or taller than the phone's screen, its layout squeezed
below its own minimum so that buttons were cut in half or pushed off its edge,
and its title bar's ✕ — the one way out of a window that has no Close of its
own — off the screen or too small to hit. Measured on a 432 × 936 px phone,
Remote Storage's content needed 893 px of width and every button on its foot
was clipped, Test detection opened 984 px wide with its title bar's ✕ past the
right edge, and the Storage dialog held its 590 px height on a 432 px-tall
sideways screen.

So in phone layout every such window takes a **phone form** (:func:`fit`):

* it covers the screen's available area exactly — its minimum and maximum
  are both pinned there, so a dialog that grows later (``adjustSize``, a
  survey filling in) cannot leave the screen again;
* its content moves into a scroll area (the window's own layout, moved whole
  onto a body widget — a widget can take another's layout, which re-parents
  every widget it manages), so a window taller or wider than the phone scrolls,
  by a drag like every other scroll area in phone layout, where it used to be
  squeezed and clipped; the body is narrowed first with :mod:`ui.phone_fit`,
  the same pass the Datasets tools take;
* a foot, outside what scrolls, holds the window's own row of buttons when its
  last row is nothing but buttons (a ``QDialogButtonBox``, or a row of push
  buttons such as *Open folder · Clear selected · Close*), so OK and Cancel
  stay on screen however far the content scrolls — and a touch-sized
  **Close ✕** that does exactly what the title bar's ✕ does (``close()``,
  which a dialog answers with ``reject``), unless the row brought down
  already holds the window's own Close or Cancel, or the window has no ✕ at
  all (a progress popup that cannot be cancelled).

:func:`unfit` gives each window its desk form back — its layout, its row of
buttons at the index it came from, its minimum, maximum and size — when phone
layout ends.

**What is left alone.** Qt's own small dialogs (a message box, an input or
progress dialog, a colour, font or file dialog) size themselves and carry
their own buttons; phone layout only keeps them on the screen
(:data:`KEEP_ON_SCREEN`, ``phone_shell.clamp_to_screen``). So does a
``QMainWindow``, whose layout is its own, and a window with no layout. A
*native* file dialog (``QFileDialog.getOpenFileName`` and friends, which on
Windows open the system's own dialog) is not a Qt widget at all — no Qt code
can resize or rearrange it. Popups, menus, tool windows and tooltips are not
windows of this kind (:func:`kind` reads the window type).

Qt-only and pane-free, like :mod:`ui.phone_fit`: ``ui.phone_shell`` finds the
windows (its sweep and its per-widget event filter) and calls in here.
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QObject, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractButton, QBoxLayout, QColorDialog, QDialogButtonBox, QErrorMessage, QFileDialog,
    QFontDialog, QHBoxLayout, QInputDialog, QLayout, QMainWindow, QMessageBox, QProgressDialog,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core import phone_layout
from ui import phone_fit

#: The property ``PhoneShell`` sets on the window it rearranges itself, which
#: is never given a phone form here.
MAIN_WINDOW_PROP = "phoneMainWindow"

#: :func:`kind`'s two answers.
FORM = "form"
KEEP_ON_SCREEN = "keep_on_screen"

#: Qt's own dialogs that size themselves and carry their own buttons.
_COMPACT = (QMessageBox, QInputDialog, QProgressDialog, QFileDialog, QColorDialog,
            QFontDialog, QErrorMessage)

#: The window types that are windows a person opens and closes. A popup (a
#: menu, a combo's list), a tool window (a popover), a tooltip, a splash
#: screen are not.
_WINDOW_TYPES = (Qt.Window, Qt.Dialog)

#: The texts that make a button in the row brought to the foot the window's
#: own way out, so no second Close is added beside it.
_CLOSER_TEXTS = frozenset({"close", "cancel", "done"})

CLOSE_TEXT = "Close ✕"

_QWIDGETSIZE_MAX = 16_777_215


def kind(w: QWidget) -> str | None:
    """What phone layout does with *w* when it is shown: :data:`FORM`,
    :data:`KEEP_ON_SCREEN`, or ``None`` when *w* is not a window it fits."""
    if not isinstance(w, QWidget) or not w.isWindow() or w.property(MAIN_WINDOW_PROP):
        return None
    if w.windowType() not in _WINDOW_TYPES or w.windowFlags() & Qt.WindowType.FramelessWindowHint:
        return None             # a frameless window draws its own chrome: not one a person opens
    if form_of(w) is not None:
        return FORM
    if isinstance(w, (*_COMPACT, QMainWindow)) or w.layout() is None:
        return KEEP_ON_SCREEN
    return FORM


def form_of(w: QWidget) -> _PhoneForm | None:
    """The phone form *w* carries, or ``None`` at its desk form."""
    for child in w.children():
        if isinstance(child, _PhoneForm):
            return child
    return None


def fitted_windows() -> list[QWidget]:
    """Every window that carries a phone form."""
    from PySide6.QtWidgets import QApplication
    out = []
    for w in QApplication.topLevelWidgets():
        try:
            if form_of(w) is not None:
                out.append(w)
        except RuntimeError:
            continue        # deleted while the list was walked
    return out


def fit(window: QWidget, avail: QRect, unit: float) -> QScrollArea | None:
    """Give *window* its phone form inside *avail* (the screen's available
    geometry) at *unit* px per phone point, or place it again if it has one.
    Returns the scroll area its content now sits in, ``None`` for a window
    :func:`kind` does not give a form."""
    if kind(window) != FORM:
        return None
    form = form_of(window) or _PhoneForm(window, avail, unit)
    form.place(avail, unit)
    return form.scroll


def unfit(window: QWidget) -> bool:
    """Give *window* its desk form back. ``True`` if it had a phone form."""
    form = form_of(window)
    if form is None:
        return False
    form.undo()
    return True


def _is_spacer(item) -> bool:
    return item.spacerItem() is not None


def _widgets_in(layout: QLayout) -> list[QWidget]:
    out = []
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item.widget() is not None:
            out.append(item.widget())
        elif item.layout() is not None:
            out.extend(_widgets_in(item.layout()))
    return out


def _only_buttons(item) -> list[QAbstractButton]:
    """The buttons of a layout item that holds buttons and nothing else
    (spacing aside); ``[]`` for any other item."""
    w = item.widget()
    if isinstance(w, QDialogButtonBox):
        return list(w.buttons())
    if w is not None:
        if w.layout() is None:
            return []
        widgets = _widgets_in(w.layout())
    elif item.layout() is not None:
        widgets = _widgets_in(item.layout())
    else:
        return []
    if not widgets or not all(isinstance(b, QAbstractButton) for b in widgets):
        return []
    return widgets


def _is_closer(button: QAbstractButton) -> bool:
    box = button.parentWidget()
    if isinstance(box, QDialogButtonBox) and box.buttonRole(button) == QDialogButtonBox.RejectRole:
        return True
    return button.text().replace("&", "").strip(" ✕").lower() in _CLOSER_TEXTS


def _closable(window: QWidget) -> bool:
    """Whether the window offers a ✕ of its own — not one whose author took
    it away (``setWindowFlag(Qt.WindowCloseButtonHint, False)``)."""
    flags = window.windowFlags()
    return not (flags & Qt.WindowType.CustomizeWindowHint) or bool(
        flags & Qt.WindowType.WindowCloseButtonHint)


def _frame_margins(window: QWidget) -> QMargins:
    handle = window.windowHandle()
    return handle.frameMargins() if handle is not None else QMargins()


class _PhoneForm(QObject):
    """One window's phone form, and everything needed to take it off again.
    A child of the window, so it goes with it."""

    def __init__(self, window: QWidget, avail: QRect, unit: float) -> None:
        super().__init__(window)
        self.setObjectName("PhoneWindowForm")
        self._win = window
        self._layout = layout = window.layout()
        layout.activate()
        self._desk_min = window.minimumSize()
        # A minimum the layout set is not the window's own: give back none and
        # the layout sets it again. One the code set is put back as it was.
        self._own_min = (self._desk_min != QSize(0, 0)
                         and self._desk_min != layout.totalMinimumSize())
        self._desk_max = window.maximumSize()
        self._desk_size = window.size()
        self._row: tuple[int, int, object] | None = None

        self.body = QWidget()
        self.body.setObjectName("PhoneWindowBody")
        self.body.setLayout(layout)              # takes it, and every widget it manages
        self.scroll = QScrollArea()
        self.scroll.setObjectName("PhoneWindowScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setWidget(self.body)
        self.foot = QWidget()
        self.foot.setObjectName("PhoneWindowFoot")
        self._foot_row = QHBoxLayout(self.foot)
        outer = QVBoxLayout(window)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.scroll, 1)
        outer.addWidget(self.foot)

        closers = self._bring_down_buttons(avail.width(), unit)
        self.close_button: QPushButton | None = None
        if _closable(window) and not closers:
            self.close_button = QPushButton(CLOSE_TEXT, self.foot)
            self.close_button.setObjectName("PhoneWindowClose")
            self.close_button.setToolTip("Close this window — what its title bar's ✕ does.")
            self.close_button.clicked.connect(lambda _=False: self._win.close())
            if self._row is None:
                self._foot_row.addStretch(1)
            self._foot_row.addWidget(self.close_button)
        self.foot.setVisible(self._foot_row.count() > 0)

    def _bring_down_buttons(self, width: int, unit: float) -> bool:
        """Move the window's last row to the foot when it is a row of buttons.
        ``True`` when that row holds the window's own Close or Cancel."""
        lay = self._layout
        if not isinstance(lay, QBoxLayout) or lay.direction() != QBoxLayout.TopToBottom:
            return False
        idx = next((i for i in range(lay.count() - 1, -1, -1) if not _is_spacer(lay.itemAt(i))), -1)
        if idx < 0:
            return False
        buttons = _only_buttons(lay.itemAt(idx))
        if not buttons:
            return False
        stretch = lay.stretch(idx)
        item = lay.takeAt(idx)
        moved = item.widget() or item.layout()
        if isinstance(moved, QWidget):
            self._foot_row.addWidget(moved, 1)
        else:
            self._foot_row.addLayout(moved, 1)
        self._row = (idx, stretch, moved)
        gap = phone_layout.touch_px(unit, phone_layout.TOUCH_GAP_PT)
        self._foot_row.setContentsMargins(gap, gap, gap, gap)
        self._foot_row.activate()
        if self.foot.minimumSizeHint().width() > width:
            self._give_row_back()               # wider than the phone: it scrolls with the rest
            return False
        return any(_is_closer(b) for b in buttons)

    def _give_row_back(self) -> None:
        if self._row is None:
            return
        idx, stretch, moved = self._row
        self._row = None
        pos = (self._foot_row.indexOf(moved) if isinstance(moved, QWidget)
               else next(i for i in range(self._foot_row.count())
                         if self._foot_row.itemAt(i).layout() is moved))
        self._foot_row.takeAt(pos)
        if isinstance(moved, QWidget):
            self._layout.insertWidget(idx, moved, stretch)
        else:
            self._layout.insertLayout(idx, moved, stretch)

    def place(self, avail: QRect, unit: float) -> None:
        """Cover *avail*, frame included, and narrow the content to it."""
        gap = phone_layout.touch_px(unit, phone_layout.TOUCH_GAP_PT)
        self._foot_row.setContentsMargins(gap, gap, gap, gap)
        self._foot_row.setSpacing(gap)
        m = _frame_margins(self._win)
        client = avail.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        win = self._win
        win.setMinimumSize(client.size())
        win.setMaximumSize(client.size())
        win.setGeometry(client)
        bar = self.scroll.verticalScrollBar().sizeHint().width()
        phone_fit.fit_content(self.body, max(0, client.width() - bar))
        self.foot.setVisible(self._foot_row.count() > 0)

    def undo(self) -> None:
        win = self._win
        phone_fit.unfit_content(self.body)
        self._give_row_back()
        holder = QWidget()
        holder.setLayout(win.layout())          # the phone form's scroll and foot go with it
        win.setLayout(self._layout)             # the desk layout, and its widgets, come home
        win.setMaximumSize(self._desk_max)
        if self._own_min:
            win.setMinimumSize(self._desk_min)
        else:
            win.setMinimumSize(0, 0)
        self._layout.invalidate()
        self._layout.activate()
        win.resize(self._desk_size.expandedTo(win.minimumSize())
                   .boundedTo(QSize(_QWIDGETSIZE_MAX, _QWIDGETSIZE_MAX)))
        holder.deleteLater()
        self.setParent(None)
        self.deleteLater()
