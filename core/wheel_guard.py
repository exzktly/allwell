"""A mouse wheel over a dropdown or a number box scrolls the page it is on, not the value in it.

Qt gives a combo box or a spin box the wheel whenever the pointer is over it, so scrolling down a long form (the
Analyze tab's) changed every field the pointer crossed on the way, with nothing saying so. The convention every
browser settled on is used here: a number box takes the wheel only once it has the keyboard focus, which a click or a
tab gives it, and a dropdown does not take it at all. Until then the wheel goes to the scroll area the field sits in, as if the field were not there. A dropdown never takes
the wheel while closed, focus or not: it keeps the focus after a choice, and the next scroll would change it again.

One filter on the application, so a field built tomorrow is covered without anyone remembering to ask.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QWidget

#: Set to ``0`` to start the app without the guard. A diagnosis switch: an access violation in a Windows session was
#: reported twice inside :meth:`_WheelGuard.eventFilter`, each time just after Review finished loading a dataset during
#: a remote run, and it does not reproduce on Linux. Whether the crash needs the guard is answered by running the same
#: steps with it off; ``allwell.main`` writes which way each session ran into the boot log, beside any crash.
SWITCH_ENV = "ALLWELL_WHEEL_GUARD"


def wanted(environ=None) -> bool:
    """Whether the guard is to be installed: always, unless :data:`SWITCH_ENV` is ``0``."""
    env = os.environ if environ is None else environ
    return env.get(SWITCH_ENV, "").strip() != "0"


#: The widgets that change a value when the wheel turns over them.
_GUARDED = (QComboBox, QAbstractSpinBox)


class _WheelGuard(QObject):
    def eventFilter(self, obj, event) -> bool:  # Qt's name
        if event.type() == QEvent.Show and isinstance(obj, _GUARDED):
            take_no_focus_from_the_wheel(obj)
            return False
        if event.type() != QEvent.Wheel or not isinstance(obj, _GUARDED):
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


def take_no_focus_from_the_wheel(widget: QWidget) -> None:
    """Make *widget* take the keyboard focus from a click or a tab, not from the wheel.

    These widgets default to ``Qt.WheelFocus``, and Qt gives a widget its focus on a real wheel event *before* any
    filter sees it (``QApplication::notify``). The first tick of a trackpad scroll that reached a dropdown therefore
    focused it, and the focus check below then let that tick and every later one change its value — the page stopped
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


def install(app: QApplication) -> None:
    """Install the guard once on *app*."""
    if getattr(app, "_aw_wheel_guard", None) is None:
        app._aw_wheel_guard = _WheelGuard(app)
        app.installEventFilter(app._aw_wheel_guard)
        for widget in app.allWidgets():
            if isinstance(widget, _GUARDED):
                take_no_focus_from_the_wheel(widget)
