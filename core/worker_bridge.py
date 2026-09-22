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

    #: The signals this call actually connected, **by name**. ``done`` and
    #: ``failed`` always are — ``failed`` even without an ``on_failed``, so a
    #: raising job still disconnects. The other two only when the caller asked
    #: to hear from them.
    #:
    #: Names rather than the ``SignalInstance`` objects themselves, and that is
    #: not a style choice: a list of signal instances does not keep their
    #: ``QObject`` alive, and ``_disconnect`` below is the only thing holding a
    #: reference to *bridge* once the worker thread has ended. Holding the
    #: instances instead let the bridge be collected and then disconnected
    #: through, which segfaults — and it segfaults precisely for the callers
    #: that follow the honest reading of this function's contract and keep no
    #: reference of their own, which is what ``tests/test_worker_bridge.py``
    #: does and how this was caught.
    connected = ["done", "failed"]

    def _disconnect() -> None:
        # Only what was connected. Disconnecting a signal with no connections
        # raises *and* emits a RuntimeWarning, and the warning survives the
        # except — so blanket-disconnecting all four printed two warnings per
        # call for every caller that wanted no progress reporting, which is
        # most of them. The guard stays for the genuinely racy case: a bridge
        # whose C++ object has gone by the time this runs.
        for name in connected:
            try:
                getattr(bridge, name).disconnect()
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
        connected.append("progress")
    if on_stage is not None:
        bridge.stage.connect(on_stage)
        connected.append("stage")

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
    here rather than inline per feature for one reason: ``runtime_app`` is under
    a line and defensive-probe ratchet that this pattern would eat (three
    ``getattr`` probes per feature).

    Never imports a feature: the workers are reached through runtime state, so
    a build without the feature simply finds nothing to stop. That is what the
    ``worker is None`` check does the work of — a profiled-out feature still
    has a slice (``AppRuntime`` constructs the known ones unconditionally, and
    ``_FeatureRuntime`` answers an unknown attribute with ``None``), it just
    never assigns a thread to it. The ``slice_ is None`` guard covers a
    *slice name* this build does not know at all.

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
        # Separate handlers, because a shared one turns "cancel() raised" into
        # "the thread was never waited on either" — the two steps have nothing
        # to do with each other and the second is what stops a live thread from
        # emitting into a widget tree that is being deleted. Logged at WARNING,
        # not DEBUG: a teardown that fails here is the shape that ends in a
        # native crash on exit, and at the app's default level a DEBUG line
        # leaves nothing behind for the bug report to carry.
        log = logging.getLogger(__name__)
        try:
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()
        except Exception:
            log.warning("teardown: cancelling %s.%s failed",
                        slice_name, attr, exc_info=True)
        try:
            if worker.isRunning():
                worker.wait(wait_ms)
        except Exception:
            log.warning("teardown: waiting on %s.%s failed",
                        slice_name, attr, exc_info=True)
        # Drop the reference so a dead QThread is not left hanging off the
        # runtime slice for whatever looks at it next.
        try:
            setattr(slice_, attr, None)
        except Exception:
            log.debug("teardown: could not clear %s.%s",
                      slice_name, attr, exc_info=True)


def stop_runtime_callable(runtime, slice_name: str, attr: str) -> None:
    """Call a stopper a feature parked on its runtime slice, if it left one.

    The sibling of :func:`stop_runtime_workers` for a background job that is
    not a ``QThread`` — the per-cell export's writer runs on a plain daemon
    thread, and stopping it means setting an event *and* putting a sentinel on
    a queue, which only the feature knows how to do. So the feature registers a
    stopper while it runs and teardown calls it through the runtime, which is
    what keeps core from naming a feature to reach it.

    Absent stopper, absent slice and a raising stopper are all non-events: a
    build without the feature simply finds nothing to call, and a teardown step
    that fails must not stop the ones after it.
    """
    slice_ = getattr(runtime, slice_name, None) if runtime is not None else None
    stop = getattr(slice_, attr, None) if slice_ is not None else None
    if stop is None:
        return
    try:
        stop()
    except Exception:
        logging.getLogger(__name__).warning(
            "teardown: %s.%s stopper failed", slice_name, attr, exc_info=True)
