"""A list row with a remove button that appears while the pointer is over it.

Two surfaces draw the recent-datasets list — Home's card and the no-dataset
placeholder — and both want the same affordance: the row is the target, and a
small × on its right edge takes it off the list. The × is hidden until the
pointer is over the row, because a column of ten rows each wearing a permanent
delete control reads as a page about deleting rather than a page about opening.

No All-Well knowledge here: a button, a callback and a tooltip go in, a row
widget comes out.

**The space is reserved whether or not the × is showing.** Revealing a widget
that a layout has to make room for reflows the row under the cursor, so the
label shifts the instant the pointer arrives — the one moment it must not. The
× lives inside a fixed-width slot and only the button inside that slot is
hidden, so the geometry is the same in both states.

**Hover is counted, not sampled.** ``QWidget.underMouse`` is a state Qt keeps
for the widget the pointer entered, and moving from the row onto the × is a
Leave on one and an Enter on the other; a handler that hid on any Leave would
take the button away as the user reached for it. So Enter and Leave are counted
across the row and every child of it, and the × shows while the count is
positive. The count is order-independent, which matters because Qt does not
promise Enter-before-Leave when the pointer crosses between a parent and its
child.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ui import theme

#: Side of the × button, in pixels. The same 18 the Variables rail's row delete
#: uses — it is the smallest square that still takes a click without demanding
#: aim, and the two controls mean the same thing in two lists.
REMOVE_PX = 18

#: Marks a widget as one :func:`removable_row` built. Read by :func:`row_entry`
#: and :func:`row_remove_button`, which have to tell a wrapped row from a bare
#: widget — the two shapes a caller gets back — without guessing from the
#: layout, since plenty of unwrapped widgets have a layout with a child in it.
_ROW_MARK = "aw_removable_row"

#: Where the row records its ×'s object name, so the accessor can find the
#: button without the caller having to remember which of the two names this
#: surface used.
_ROW_REMOVE_NAME = "aw_removable_row_x"


class _HoverReveal(QObject):
    """Shows *target* while the pointer is anywhere over the watched widgets.

    The watcher is parented to the row, so it lives exactly as long as the row
    does. It keeps its count on the object rather than on the row, and nothing
    else reads it: a row that is hidden while the pointer is over it (a repaint
    replacing the whole list is the ordinary way that happens) is destroyed
    along with its watcher, so a stale count cannot outlive anything.
    """

    def __init__(self, row: QWidget, target: QWidget) -> None:
        super().__init__(row)
        self._target = target
        self._depth = 0

    def eventFilter(self, obj, event) -> bool:  # Qt naming
        kind = event.type()
        if kind == QEvent.Enter:
            self._depth += 1
        elif kind == QEvent.Leave:
            # Clamped at zero: an unpaired Leave — a widget hidden under the
            # pointer, then shown again — would otherwise push the count
            # negative and leave the × unreachable for the rest of the row's
            # life.
            self._depth = max(0, self._depth - 1)
        else:
            return False
        self._target.setVisible(self._depth > 0)
        return False


def remove_button(parent: QWidget, object_name: str, tooltip: str) -> QPushButton:
    """The × itself, hidden, sized and named for *object_name*'s stylesheet.

    Separate from :func:`removable_row` so the button's shape — its size, its
    glyph, its hidden-until-hovered starting state — is stated once, for a
    caller that builds its own row around it. :func:`row_remove_button` is how
    a caller holding a finished row gets back to it.
    """
    button = QPushButton("✕", parent)
    button.setObjectName(object_name)
    button.setFixedSize(QSize(REMOVE_PX, REMOVE_PX))
    button.setCursor(Qt.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
    button.setVisible(False)
    return button


def removable_row(widget: QWidget, on_remove, *, object_name: str,
                  tooltip: str = "", parent: QWidget | None = None) -> QWidget:
    """*widget*, plus a × that runs *on_remove*, as one row.

    *widget* is reparented into the row. Returns the row, which is what the
    caller adds to its layout.

    The × is **enabled regardless of** *widget*'s own enabled state. They are
    siblings rather than parent and child precisely so that they can differ:
    the row a user most wants off a recent-datasets list is the one whose
    folder is gone, and that row's open button is disabled.
    """
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(theme.Spacing.xs)
    widget.setParent(row)
    layout.addWidget(widget, 1)

    # A fixed-width slot rather than the button itself, so hiding the button
    # takes no space away from the row beside it. See the module docstring.
    slot = QWidget(row)
    slot.setFixedWidth(REMOVE_PX)
    slot_layout = QHBoxLayout(slot)
    slot_layout.setContentsMargins(0, 0, 0, 0)
    slot_layout.setSpacing(0)
    button = remove_button(slot, object_name, tooltip)
    slot_layout.addWidget(button)
    layout.addWidget(slot, 0)

    if callable(on_remove):
        button.clicked.connect(lambda _checked=False, _fn=on_remove: _fn())

    row.setProperty(_ROW_MARK, True)
    row.setProperty(_ROW_REMOVE_NAME, object_name)

    watcher = _HoverReveal(row, button)
    for watched in (row, widget, slot, button):
        watched.installEventFilter(watcher)
    return row


def row_entry(widget: QWidget) -> QWidget:
    """The thing :func:`removable_row` wrapped, or *widget* when it wrapped none.

    A named accessor because a caller holding "the row at index 2" has no way
    to know which shape it got: a row with no remove callback is the bare
    widget, and one with a callback is the container around it. Every reader —
    the tests included — asks here rather than each spelling its own
    ``layout().itemAt(0).widget()``.
    """
    if widget.property(_ROW_MARK) is not True:
        return widget
    item = widget.layout().itemAt(0)
    inner = item.widget() if item is not None else None
    return inner if inner is not None else widget


def row_remove_button(widget: QWidget) -> QPushButton | None:
    """The × on *widget*, or ``None`` when it carries none."""
    if widget.property(_ROW_MARK) is not True:
        return None
    return widget.findChild(QPushButton, widget.property(_ROW_REMOVE_NAME))
