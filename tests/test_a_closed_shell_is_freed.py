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


def test_a_closed_shell_with_the_playground_is_collected(qapp, mini_dataset):
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

    from review.features.image_browser.playground.tab_view import build_playground_tab

    # Settle what earlier tests in this process left queued before taking the
    # baseline. Under xdist a worker runs many files back to back, and a
    # deferred delete or timer of theirs landing inside this test's own flush
    # read once as a `QFrame` this shell had left behind.
    _flush(qapp)
    before = {id(w) for w in QApplication.topLevelWidgets()}
    ctx, window = minimal_shell(_NoBot())
    load_dataset(ctx, mini_dataset)
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    build_playground_tab(ctx, frame)
    qapp.processEvents()

    ref = weakref.ref(window)
    window.close()
    window.deleteLater()
    del ctx, window, frame
    _flush(qapp)
    gc.collect()
    _flush(qapp)
    gc.collect()

    left = [type(w).__name__ for w in QApplication.topLevelWidgets()
            if id(w) not in before]
    assert left == [], f"top-level widgets outlived the shell: {left}"
    assert ref() is None, "a closed shell with the Playground built is still alive"
