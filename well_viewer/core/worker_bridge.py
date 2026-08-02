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
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal


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
