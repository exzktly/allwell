"""A viewport turned back to drag-scrolling while its old guard waits to be deleted.

``ui.phone_shell.set_drag_scroll(vp, False)`` never releases a grab from inside the call that asked: the guard's
timer does it later (``_DragOwnerGuard._release_grab``), then the guard deletes itself with ``deleteLater``. Until
that deferred delete runs, the released guard is still the viewport's child. Turned on again in that window, the
viewport used to be grabbed afresh while ``findChild`` handed back the dying guard, so no new guard was installed;
once the old one was deleted the viewport scrolled by a drag with nothing refusing drags that start on a plate or
a slider, and the next ``set_drag_scroll(vp, False)`` found no guard to release the grab with, so it was held for
good. The wrong implementation this rejects is that one: a lookup that counts a released guard as live.

Behaviour rather than layout, so it is not in ``tests/_lanes.py::UI_LANE_FILES`` and CI runs it.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

from ui import phone_shell
from ui.phone_shell import _DragOwnerGuard, set_drag_scroll


def _area(qtbot):
    area = QScrollArea()
    qtbot.addWidget(area)
    area.setWidget(QLabel("x" * 200))
    area.resize(200, 100)
    return area


def _guards(vp):
    return vp.findChildren(_DragOwnerGuard, options=Qt.FindDirectChildrenOnly)


def _run_deferred_deletes():
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QApplication.processEvents()


def test_turned_on_again_before_the_old_guard_is_deleted_it_has_a_guard_and_can_be_released(qtbot):
    area = _area(qtbot)
    vp = area.viewport()
    set_drag_scroll(vp, True)
    set_drag_scroll(vp, False)
    QApplication.processEvents()                        # the release timer fires; the delete is still deferred
    assert vp.property(phone_shell._P_DRAG_GRABBED) is None
    assert len(_guards(vp)) == 1                        # released, but not yet deleted

    set_drag_scroll(vp, True)                           # in that window
    _run_deferred_deletes()

    live = _guards(vp)
    assert len(live) == 1, f"drag-scrolling again with {len(live)} guard(s); a grab with no guard refuses nothing"
    assert vp.property(phone_shell._P_DRAG_GRABBED) is True

    set_drag_scroll(vp, False)
    QApplication.processEvents()
    assert vp.property(phone_shell._P_DRAG_GRABBED) is None, "turned off, the grab was never released"
