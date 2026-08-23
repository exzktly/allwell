"""Run one callable off the GUI thread and deliver its result back on it.

A worker thread has no Qt event loop, so a result cannot be handed to the GUI by
calling a callback directly and cannot be scheduled with
``QTimer.singleShot(0, …)`` — that silently drops the call (ARCHITECTURE §5, and
the reason every background job in this app goes through a queued signal). The
shape that works is always the same: a short-lived ``QObject`` whose ``done`` /
``failed`` signals are connected on the GUI thread and emitted from the worker.

This is that shape, once. It is **one-shot**: both connections are dropped after
the first fire, so a button pressed repeatedly — which is exactly what a preview
control is for — does not leak a connected ``QObject`` holding the callbacks and,
through them, the widgets and the app.

Lives in ``core`` rather than in the Playground, its present sole caller, because
the Playground is a removable feature: a bridge every background job in the app
may want cannot sit inside a package that a build profile can drop.

:class:`GuiLogRelay` at the bottom of this module is the same shape for the one
case that is not a job at all: a ``logging.Handler``. A handler is entered on
whatever thread called ``logger.info(...)``, which for this app includes every
worker in it, so a handler that writes to a widget needs exactly this route.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, Signal


class WorkerBridge(QObject):
    """Marshals one worker-thread result onto the GUI thread."""

    done = Signal(object)
    failed = Signal(str)
    # Interim reports while the job runs — a (done, total) count, and the name of
    # the phase it has reached. Same queued-signal route as ``done``: a worker
    # must never touch a widget, so anything on screen has exactly one writer and
    # it is the GUI thread.
    progress = Signal(int, int)
    stage = Signal(str)


class _Reporter:
    """What a reporting worker is handed: two methods, no widgets.

    Deliberately not the bridge itself. A worker given the ``QObject`` could
    connect to it, read it, or keep it alive past the call; given this it can
    only say how far along it is and what it is doing.
    """

    __slots__ = ("_bridge",)

    def __init__(self, bridge: WorkerBridge) -> None:
        self._bridge = bridge

    def progress(self, done: int, total: int) -> None:
        self._bridge.progress.emit(int(done), int(total))

    def stage(self, name: str) -> None:
        self._bridge.stage.emit(str(name))


def run_off_thread(fn, *, on_done, on_failed=None, on_progress=None,
                   on_stage=None) -> WorkerBridge:
    """Call *fn()* on a daemon thread; deliver the outcome on the GUI thread.

    ``on_done(result)`` runs for a normal return, ``on_failed(message)`` for any
    exception — the exception is never re-raised off the GUI thread, where
    nothing would catch it. Returns the bridge; **keep a reference to it** for
    the duration of the call (typically on ``ctx.runtime.<feature>``), because a
    garbage-collected ``QObject`` never delivers.

    Pass ``on_progress(done, total)`` and/or ``on_stage(name)`` to hear from the
    job while it runs. With either given, *fn* is called with **one argument** —
    a :class:`_Reporter` exposing ``.progress(done, total)`` and
    ``.stage(name)``. With neither, *fn* is called with no arguments, exactly as
    before: a caller that wants no reporting does not have to change its
    signature to keep working.
    """
    bridge = WorkerBridge()

    def _disconnect() -> None:
        # Disconnecting a signal with no connections raises, so guard it — this
        # also runs when a caller passed no on_failed / on_progress / on_stage.
        for sig in (bridge.done, bridge.failed, bridge.progress, bridge.stage):
            try:
                sig.disconnect()
            except (TypeError, RuntimeError):
                pass

    def _handle_done(result) -> None:
        try:
            on_done(result)
        finally:
            _disconnect()

    def _handle_failed(message: str) -> None:
        try:
            if on_failed is not None:
                on_failed(message)
        finally:
            _disconnect()

    bridge.done.connect(_handle_done)
    bridge.failed.connect(_handle_failed)
    if on_progress is not None:
        bridge.progress.connect(on_progress)
    if on_stage is not None:
        bridge.stage.connect(on_stage)

    reporter = (None if (on_progress is None and on_stage is None)
                else _Reporter(bridge))

    def _run() -> None:
        try:
            bridge.done.emit(fn() if reporter is None else fn(reporter))
        except Exception as exc:      # never raise off the GUI thread
            bridge.failed.emit(str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return bridge


class GuiLogRelay(QObject):
    """Hand one formatted log line to a GUI-thread callable, from any thread.

    A ``logging.Handler`` runs on whichever thread emitted the record. Every
    background worker in this app logs — the remote runner's batch loop and its
    fetcher, the smFISH and cell-browser loaders, the gating ``QThread``s, and
    every ``swallowed()`` call any of them makes — so a handler that appends to a
    ``QTextEdit`` is entered off the GUI thread routinely. Touching the widget
    there is illegal, and ``QTimer.singleShot(0, …)`` is not the escape hatch it
    reads as: it builds its timer on the *calling* thread, so the callback never
    fires and the stray ``QObject`` is left owned by a thread that is about to
    end — which is what raises ``QObject::killTimer: Timers cannot be stopped
    from another thread`` at teardown.

    Construct one on the GUI thread (the handler's ``__init__`` is the natural
    place, since the handler itself is built there) and call :meth:`post` from
    anywhere. The connection is explicitly queued rather than automatic so that
    delivery is deferred to the event loop from *both* sides: a line logged on
    the GUI thread must not append re-entrantly in the middle of whatever was
    running when it was logged, which is the one property the ``singleShot``
    spelling did have.

    Keep a reference to the relay for as long as the handler lives — a
    garbage-collected ``QObject`` delivers nothing. Holding it on the handler is
    enough.
    """

    _line = Signal(str)

    def __init__(self, deliver) -> None:
        super().__init__()
        self._line.connect(deliver, Qt.QueuedConnection)

    def post(self, text: str) -> None:
        """Queue *text* for delivery on the thread that built this relay."""
        self._line.emit(text)


def stop_runtime_workers(runtime, slice_name: str, attrs, *, wait_ms: int = 250) -> None:
    """Cancel and join the QThreads a runtime slice is holding.

    App-close teardown has to stop several features' workers, and every one of
    them is reached the same way: a runtime slice, an attribute holding a
    thread, ``cancel()`` if it has one, then a bounded ``wait``. Written once
    here rather than inline per feature for two reasons — ``runtime_app`` is
    under a line and defensive-probe ratchet that this pattern would eat
    (three ``getattr`` probes per feature), and a feature that is profiled out
    has no slice at all, so the "is it there?" question belongs in one place.

    Never imports a feature: the workers are reached through runtime state, so
    a build without the feature simply finds nothing to stop.

    The wait is short on purpose. ``cancel()`` is what makes the stop safe —
    a cancelled worker disowns whatever it produces — so waiting is only a
    courtesy to let a thread unwind at its next checkpoint. Blocking the window
    close for seconds while a worker finishes reading a well is a worse
    outcome than letting a daemon thread exit on its own after the pane is
    gone, and the wait is what a user experiences as "the app won't quit".
    """
    slice_ = getattr(runtime, slice_name, None)
    if slice_ is None:
        return
    for attr in attrs:
        worker = getattr(slice_, attr, None)
        if worker is None:
            continue
        try:
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()
            if worker.isRunning():
                worker.wait(wait_ms)
        except Exception:
            logging.getLogger(__name__).debug(
                "teardown: stopping %s.%s failed", slice_name, attr, exc_info=True)
