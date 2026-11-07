"""A mouse wheel over a dropdown or a number box scrolls the page it is on, not the value in it.

Qt gives a combo box or a spin box the wheel whenever the pointer is over it, so scrolling down a long form (the
Analyze tab's) changed every field the pointer crossed on the way, with nothing saying so. The convention every
browser settled on is used here: such a field takes the wheel only once it has the keyboard focus, which a click or a
tab gives it. Until then the wheel goes to the scroll area the field sits in, as if the field were not there.

One filter on the application, so a field built tomorrow is covered without anyone remembering to ask.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QWidget

#: The widgets that change a value when the wheel turns over them.
_GUARDED = (QComboBox, QAbstractSpinBox)


class _WheelGuard(QObject):
    def eventFilter(self, obj, event) -> bool:  # Qt's name
        if event.type() != QEvent.Wheel or not isinstance(obj, _GUARDED) or obj.hasFocus():
            return False
        if isinstance(obj, QComboBox) and obj.view().isVisible():
            return False                                   # the open list is the combo's own
        area = _scroll_area_of(obj)
        if area is not None:
            _forward(event, area)
        return True                                        # the field never sees it


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


def install(app: QApplication) -> None:
    """Install the guard once on *app*."""
    if getattr(app, "_aw_wheel_guard", None) is None:
        app._aw_wheel_guard = _WheelGuard(app)
        app.installEventFilter(app._aw_wheel_guard)
