"""The log drawer's handler lets go of the logger when its widget is destroyed.

Row Q4 of the remediation plan (deleted when its last row closed; ``git log --
_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` recovers it) measured what a
closed window still leaves behind. With no dataset loaded, each window left a
``_GUILogHandler`` on the ``review`` logger, holding its relay and the dead log
widget's wrapper: the handler detached only when the *next* record failed to
reach the widget, and a window closed with nothing logged after it never got
one. It now detaches on the widget's ``destroyed`` signal as well.
"""

from __future__ import annotations

import logging


def _flush(qapp) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qapp.processEvents()


def test_the_handler_detaches_when_its_widget_is_destroyed(qapp):
    from PySide6.QtWidgets import QTextEdit

    from review.core.status_view import _GUILogHandler

    logger = logging.getLogger("review")
    widget = QTextEdit()
    handler = _GUILogHandler(widget)
    logger.addHandler(handler)
    try:
        widget.deleteLater()
        del widget
        _flush(qapp)
        # Nothing has been logged since the widget went.
        assert handler not in logger.handlers
    finally:
        if handler in logger.handlers:
            logger.removeHandler(handler)
