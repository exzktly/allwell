"""A closed minimal shell with a feature built in it is freed — in the fast lane.

``tests/test_a_closed_review_app_is_freed.py`` holds the full app to this, and
builds a ``ReviewApp`` to do it, so it runs in the manually dispatched slow
lane. Row Q4 of the remediation plan (deleted when its last row closed; ``git log --
_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` recovers it) asks for
the half that can be asserted without one to run on every push. The roots Q1
found were all of one kind — a widget with no parent, or a callable the Qt side
keeps, holding a closure over the context — and a feature built into the
minimal shell (``tests/_minimal_shell.py``) can carry either. So: build the
feature, close the window, and require that the window is collected and that no
top-level widget is left behind.

The Playground is the feature mounted here because it is the one whose tests
run on the shell. Like the slow-lane file, this avoids ``qtbot``, which would
hold the window that is being measured.
"""

from __future__ import annotations

import gc
import weakref

from tests._minimal_shell import load_dataset, minimal_shell


class _NoBot:
    """Stands in for ``qtbot``, whose registry would keep the window alive."""

    def addWidget(self, _widget) -> None:
        return None


def _flush(qapp) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    for _ in range(5):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qapp.processEvents()


def _address(widget) -> int:
    import shiboken6

    return shiboken6.getCppPointer(widget)[0]


def _predates(widget, before: set) -> bool:
    """Whether *widget*, or a widget it hangs from, existed before the shell was built.

    By C++ address over every widget, not by the Python ``id`` of the top-level ones. A combo box an earlier test
    in the same process left alive creates its drop-down container — a new top-level window — the first time
    anything asks it for its view; under xdist that happened inside this test, and the container read as left
    behind by a shell that never created its combo (a ``QComboBoxPrivateContainer`` whose parent is a
    ``QComboBox``). The shell's own widgets are all created after the baseline, so a leak of theirs still counts.
    """
    while widget is not None:
        if _address(widget) in before:
            return True
        widget = widget.parentWidget()
    return False


def _describe(widget) -> str:
    """Enough to tell which widget outlived the shell from a CI log alone: the Python type can be a base class
    (a ``QFrame`` for any Qt-private frame subclass), so the Qt class, name, parent and window type say more."""
    parent = widget.parent()
    return (f"{type(widget).__name__}<{widget.metaObject().className()}> name={widget.objectName()!r} "
            f"parent={type(parent).__name__ if parent is not None else None}"
            f"<{parent.metaObject().className() if parent is not None else ''}> "
            f"flags={int(widget.windowFlags()):#x} visible={widget.isVisible()}")


def _leftovers(qapp, mini_dataset, during_build=None):
    """Build the shell with the Playground, close it, and return ``(what outlived it, a weakref to it)``."""
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

    from review.features.image_browser.playground.tab_view import build_playground_tab

    # Settle what earlier tests in this process left queued before taking the
    # baseline: under xdist a worker runs many files back to back.
    _flush(qapp)
    before = {_address(w) for w in QApplication.allWidgets()}
    ctx, window = minimal_shell(_NoBot())
    load_dataset(ctx, mini_dataset)
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    build_playground_tab(ctx, frame)
    if during_build is not None:
        during_build()
    qapp.processEvents()

    ref = weakref.ref(window)
    window.close()
    window.deleteLater()
    del ctx, window, frame
    _flush(qapp)
    gc.collect()
    _flush(qapp)
    gc.collect()
    return [_describe(w) for w in QApplication.topLevelWidgets() if not _predates(w, before)], ref


def test_a_closed_shell_with_the_playground_is_collected(qapp, mini_dataset):
    left, ref = _leftovers(qapp, mini_dataset)
    assert left == [], f"top-level widgets outlived the shell: {left}"
    assert ref() is None, "a closed shell with the Playground built is still alive"


def test_a_popup_an_earlier_widget_creates_meanwhile_is_not_this_shell_s(qapp, mini_dataset):
    """What failed under xdist: a combo box an earlier test left alive created its drop-down container while
    this shell was up. That container is a new top-level window, and it is not this shell's."""
    from PySide6.QtWidgets import QComboBox, QWidget

    earlier = QWidget()
    combo = QComboBox(earlier)
    try:
        left, _ref = _leftovers(qapp, mini_dataset, during_build=combo.view)
        assert left == []
    finally:
        earlier.deleteLater()
        _flush(qapp)


def test_a_window_the_shell_leaks_is_still_caught(qapp, mini_dataset):
    """The baseline excludes only what predates the shell: a window made while it was up, and kept, still counts."""
    from PySide6.QtWidgets import QWidget

    kept = []
    try:
        left, _ref = _leftovers(qapp, mini_dataset, during_build=lambda: kept.append(QWidget()))
        assert [d.split(" ")[0] for d in left] == ["QWidget<QWidget>"]
    finally:
        for widget in kept:
            widget.deleteLater()
        _flush(qapp)
