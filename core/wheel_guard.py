"""A mouse wheel over a dropdown or a number box in a form scrolls the form, not the value in it.

Qt gives a combo box or a spin box the wheel whenever the pointer is over it, so scrolling down a long form (the
Analyze tab's) changed every field the pointer crossed on the way, with nothing saying so. The convention every
browser settled on is used here: a number box takes the wheel only once it has the keyboard focus, which a click or a
tab gives it, and a dropdown does not take it at all. Until then the wheel goes to the scroll area the field sits in,
as if the field were not there. A dropdown never takes the wheel while closed, focus or not: it keeps the focus after
a choice, and the next scroll would change it again.

**Scoped to the forms that ask for it, one filter per field.** :func:`keep_guarding` puts the filter on each dropdown
and number box inside a form, and on the form itself only to notice fields added later (a layout change re-walks it).
It was one filter on the whole application, so a field built anywhere was covered — and a Windows session then
crashed twice with an access violation inside that filter, each time just after Review finished loading a dataset
during a remote run, every other thread idle; started without the filter (``ALLWELL_WHEEL_GUARD=0``) it ran with no
crash. An application filter written in Python is called for every event every object on the main thread receives,
Review's pages and the objects Qt is destroying after a load among them. A filter on a field is called for that
field's events alone.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QWidget

#: The widgets that change a value when the wheel turns over them.
_GUARDED = (QComboBox, QAbstractSpinBox)

#: Set on a field once it carries the filter, so a re-walk does not install it twice.
_MARK = "_aw_wheel_guarded"


class _WheelGuard(QObject):
    """On a guarded field: the wheel goes to the scroll area unless the field is a number box with the focus."""

    def eventFilter(self, obj, event) -> bool:  # Qt's name
        if event.type() != QEvent.Wheel:
            return False
        # A number box takes the wheel once it has the keyboard focus. A dropdown never does: it keeps the focus after
        # a choice, and "focused" then meant every dropdown the person had used went on changing under the pointer as
        # the form was scrolled. Its open list is a window of its own and gets the wheel directly.
        if isinstance(obj, QAbstractSpinBox) and obj.hasFocus():
            return False
        area = _scroll_area_of(obj)
        if area is not None:
            _forward(event, area)
        return True                                        # the field never sees it


class _TreeWatcher(QObject):
    """On a form's root: a layout change may have added fields, so the form is walked again."""

    def __init__(self, root: QWidget, guard: _WheelGuard) -> None:
        super().__init__(root)
        self._root, self._guard = root, guard

    def eventFilter(self, obj, event) -> bool:  # Qt's name
        if event.type() in (QEvent.LayoutRequest, QEvent.Show):
            _guard_fields(self._root, self._guard)
        return False


def keep_guarding(root: QWidget) -> int:
    """Guard every dropdown and number box under *root* now, and every one added under it later. The number guarded
    now."""
    guard = getattr(root, "_aw_wheel_guard", None)
    if guard is None:
        guard = root._aw_wheel_guard = _WheelGuard(root)
        root._aw_wheel_watcher = _TreeWatcher(root, guard)
        root.installEventFilter(root._aw_wheel_watcher)
    return _guard_fields(root, guard)


def _guard_fields(root: QWidget, guard: _WheelGuard) -> int:
    added = 0
    for kind in _GUARDED:
        for field in root.findChildren(kind):
            if field.property(_MARK):
                continue
            field.setProperty(_MARK, True)
            take_no_focus_from_the_wheel(field)
            field.installEventFilter(guard)
            added += 1
    return added


def take_no_focus_from_the_wheel(widget: QWidget) -> None:
    """Make *widget* take the keyboard focus from a click or a tab, not from the wheel.

    These widgets default to ``Qt.WheelFocus``, and Qt gives a widget its focus on a real wheel event *before* any
    filter sees it (``QApplication::notify``). The first tick of a trackpad scroll that reached a dropdown therefore
    focused it, and the focus check above then let that tick and every later one change its value — the page stopped
    scrolling the moment a dropdown came under the pointer. A synthetic event does not do this, which is how a test
    sending one passed while the app did not."""
    if widget.focusPolicy() == Qt.WheelFocus:
        widget.setFocusPolicy(Qt.StrongFocus)


def _scroll_area_of(widget: QWidget) -> QAbstractScrollArea | None:
    node = widget.parentWidget()
    while node is not None:
        if isinstance(node, QAbstractScrollArea):
            return node
        node = node.parentWidget()
    return None


def _forward(event: QWheelEvent, area: QAbstractScrollArea) -> None:
    """Hand *event* to *area*'s viewport, where the wheel scrolls it, as if it had arrived there."""
    viewport = area.viewport()
    here = viewport.mapFromGlobal(event.globalPosition().toPoint())
    moved = QWheelEvent(QPointF(here), event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                        event.buttons(), event.modifiers(), event.phase(), event.inverted())
    QApplication.sendEvent(viewport, moved)
