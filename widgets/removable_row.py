"""A list row with a remove button that appears while the pointer is over it —
and is always drawn where there is no pointer to hover with.

Two surfaces draw the recent-datasets list — Home's card and the no-dataset
placeholder — and both want the same affordance: the row is the target, and a
small × on its right edge takes it off the list. The × is hidden until the
pointer is over the row, because a column of ten rows each wearing a permanent
delete control reads as a page about deleting rather than a page about opening.

**Hover is a shortcut to tidiness, not the only route.** A layout with no
hover (a touch screen) has no way to reveal the ×, so the row declares an
alternate form (``widgets.form_registry.register``) in which the × is always
drawn; whoever listens for forms — the application's phone shell — switches
it. The row does not know which layout that is.

No All-Well knowledge here: a button, a callback and a tooltip go in, a row
widget comes out.

**The space is reserved whether or not the × is showing.** Revealing a widget
that a layout has to make room for reflows the row under the cursor, so the
label shifts the instant the pointer arrives — the one moment it must not. The
× lives inside a fixed-width slot and only the button inside that slot is
hidden, so the geometry is the same in both states.

**Hover is the row's own Enter and Leave, never an event filter.** Qt sends a
widget one Enter when the pointer arrives anywhere over it and one Leave when
the pointer leaves its whole area; crossing between the row and one of its
children (the entry onto the ×) sends Enter/Leave to the children only, so the
row stays entered and the × stays reachable. The row therefore overrides its own
``enterEvent``/``leaveEvent`` and nothing else watches anything.

An earlier version did this with a Python ``eventFilter`` installed on the row
and its three children, and that crashed the app with an access violation on
Windows (``event.type()`` inside the filter). Both lists that use these rows
rebuild themselves by dropping the old rows synchronously, and Qt keeps
delivering events to an object's event filters *while that object is being
destroyed* — Hide, ParentChange, ChildRemoved and finally Destroy, on a button
whose C++ type has already been demoted to a bare ``QWidget``. A Python filter
there means PySide wrapping half-destroyed objects on every rebuild.
``review/core/split_view.py`` records the same crash from an app-global filter.
An override on the row itself is not reached once its destructor has started,
because the Python subclass's part of the object is gone first.
"""

from __future__ import annotations

import weakref

import shiboken6
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ui import theme
from widgets import form_registry

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


class _RemovableRow(QWidget):
    """The row container: shows *target* while the pointer is over the row.

    Over the row means over any part of it, children included — see the module
    docstring for why the row's own Enter/Leave are enough and why this must
    not be an event filter.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._target: QWidget | None = None
        #: The form with no hover: the × is drawn whether or not the pointer
        #: is over the row (:meth:`setAlwaysShown`).
        self._always = False

    def setAlwaysShown(self, on: bool) -> None:
        """Draw the × permanently (*on*), or only while the row is hovered."""
        self._always = bool(on)
        if self._target is not None:
            self._target.setVisible(self._always or self.underMouse())

    def isAlwaysShown(self) -> bool:
        return self._always

    def enterEvent(self, event) -> None:  # Qt naming
        if self._target is not None:
            self._target.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # Qt naming
        if self._target is not None:
            self._target.setVisible(self._always)
        super().leaveEvent(event)


class _AlwaysShown:
    """The row's alternate form, as ``form_registry.register`` wants it. It
    holds the row weakly: the registry keys its callables by widget, and a
    callable that held the row would keep every row ever built."""

    def __init__(self, row: _RemovableRow) -> None:
        self._row = weakref.ref(row)

    def __call__(self, on: bool) -> None:
        row = self._row()
        if row is not None and shiboken6.isValid(row):
            row.setAlwaysShown(on)


def remove_button(parent: QWidget, object_name: str, tooltip: str) -> QPushButton:
    """The × itself, hidden, sized and named for *object_name*'s stylesheet.

    Separate from :func:`removable_row` so the button's shape — its size, its
    glyph, its hidden-until-hovered starting state — is stated once, for a
    caller that builds its own row around it. :func:`row_remove_button` is how
    a caller holding a finished row gets back to it.
    """
    from widgets import icons
    button = QPushButton(parent)
    button.setIcon(icons.make_icon("x", 12))
    button.setAccessibleName(tooltip or "Remove")
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
    row = _RemovableRow(parent)
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

    row._target = button
    form_registry.register(row, _AlwaysShown(row))
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
