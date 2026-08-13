"""A log line emitted on a worker thread reaches the widget, and builds no timer.

The static half of this rule is ``tests/test_log_handler_marshalling.py``. This
is the behavioural half, and it is the one that pins the *silent* bug: while both
handlers marshalled with ``QTimer.singleShot(0, …)``, no record logged from any
worker thread ever reached either log view. The timer was created on the emitting
thread, which has no event loop, so the callback never ran — and the abandoned
``QObject`` is what produced ``QObject::killTimer: Timers cannot be stopped from
another thread`` when that thread was torn down. Clicking Stop during a batched
remote run tears several down at once, which is how it was reported.

Both handlers are exercised, from one parametrised set of cases, so neither can
be fixed while the other is left behind — they were written from each other once
already:

* ``allwell._RingHandler`` — the shell's Application Log drawer. Attached to
  ``well_viewer`` *and* root, so it is the one that sees ``services.remote_runner``.
* ``well_viewer.core.status_view._GUILogHandler`` — Review's bottom log strip.
  Attached to ``well_viewer`` only, at DEBUG, gated on nothing.

The second test asserts on the *outbound call* rather than on the outcome
(ARCHITECTURE §5): the outcome of getting this wrong is a native abort, which is
not something a test process survives to report.
"""

from __future__ import annotations

import logging
import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication, QTextEdit

import allwell
from well_viewer.core import status_view as SV


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _OpenDrawer:
    """Stands in for the shell's log Drawer, open."""

    def isVisible(self) -> bool:
        return True


def _make_gui_log_handler():
    """Review's status-strip handler, over a real QTextEdit."""
    edit = QTextEdit()
    handler = SV._GUILogHandler(edit)
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler, edit.toPlainText


def _make_ring_handler():
    """The shell's drawer handler, over a stub app carrying the drawer's parts.

    ``_attach_log_ring_buffer`` builds the handler and attaches it to the real
    ``well_viewer`` and root loggers; it is pulled straight back off both, since
    these tests drive ``handler.handle`` directly and must not leave a handler
    behind on a process-wide logger.
    """
    app = allwell.AllWellApp.__new__(allwell.AllWellApp)
    app._log_ring = []
    app._log_ring_attached = False
    app._log_view = QTextEdit()
    app._log_drawer = _OpenDrawer()
    app._attach_log_ring_buffer()

    handler = None
    for name in ("well_viewer", ""):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            if type(h).__name__ == "_RingHandler":
                handler = h
                lg.removeHandler(h)
    assert handler is not None, "_attach_log_ring_buffer attached no _RingHandler"
    handler.setFormatter(logging.Formatter("%(message)s"))
    # Held so the app (and with it the view the handler writes into) outlives
    # the fixture — the handler reaches the widget only through this object.
    handler._test_app = app
    return handler, app._log_view.toPlainText


SUBJECTS = {
    "status_view._GUILogHandler": _make_gui_log_handler,
    "allwell._RingHandler": _make_ring_handler,
}


@pytest.fixture(params=sorted(SUBJECTS), ids=sorted(SUBJECTS))
def subject(request, qapp):
    return SUBJECTS[request.param]()


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("well_viewer.test", logging.INFO,
                             __file__, 1, msg, None, None)


def _drain(qapp) -> None:
    """Let queued cross-thread deliveries land."""
    for _ in range(5):
        qapp.processEvents()


def test_a_worker_thread_line_reaches_the_widget(qapp, subject):
    """The silent half of the bug: these lines used to be dropped entirely."""
    handler, text_of = subject
    thread = threading.Thread(target=lambda: handler.handle(_record("from-a-worker")))
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "the emitting thread never finished"

    _drain(qapp)

    assert "from-a-worker" in text_of(), (
        "a record logged on a worker thread never reached the log view — the "
        "handler marshalled with QTimer.singleShot, whose timer is created on "
        "the emitting thread and never fires there"
    )


def test_no_timer_is_created_on_the_emitting_worker_thread(qapp, subject, monkeypatch):
    """The crash half: the abandoned timer is what killTimer complains about."""
    handler, _text_of = subject
    main_thread = QThread.currentThread()
    seen: list[object] = []
    real = QTimer.singleShot

    def _spy(*args, **kwargs):
        seen.append(QThread.currentThread())
        return real(*args, **kwargs)

    monkeypatch.setattr(QTimer, "singleShot", staticmethod(_spy))

    thread = threading.Thread(target=lambda: handler.handle(_record("timer-check")))
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "the emitting thread never finished"

    off_thread = [t for t in seen if t is not main_thread]
    assert not off_thread, (
        f"{len(off_thread)} QTimer(s) were created on the emitting worker "
        "thread. Such a timer never fires, and destroying it as the thread ends "
        "raises 'QObject::killTimer: Timers cannot be stopped from another "
        "thread'."
    )

    _drain(qapp)


def test_a_gui_thread_line_still_reaches_the_widget(qapp, subject):
    """The fix must not cost the case that already worked."""
    handler, text_of = subject

    handler.handle(_record("from-the-gui-thread"))
    _drain(qapp)

    assert "from-the-gui-thread" in text_of()


def test_the_ring_keeps_a_worker_line_even_with_the_drawer_shut(qapp):
    """The drawer's visibility gates the widget, never the history.

    The gate moved out of ``emit`` (which runs on the logging thread and must
    not read Qt widget state) into ``_append_log_line`` (which runs on the GUI
    thread). The ring buffer the drawer re-fills from on each open is filled
    before either.
    """
    handler, _text_of = _make_ring_handler()
    app = handler._test_app
    app._log_drawer = None                      # never opened this session

    thread = threading.Thread(target=lambda: handler.handle(_record("shut-drawer")))
    thread.start()
    thread.join(timeout=10)
    _drain(qapp)

    assert any("shut-drawer" in line for line in app._log_ring)
    assert "shut-drawer" not in app._log_view.toPlainText()
