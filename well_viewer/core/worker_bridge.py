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


def run_off_thread(fn, *, on_done, on_failed=None) -> WorkerBridge:
    """Call *fn()* on a daemon thread; deliver the outcome on the GUI thread.

    ``on_done(result)`` runs for a normal return, ``on_failed(message)`` for any
    exception — the exception is never re-raised off the GUI thread, where
    nothing would catch it. Returns the bridge; **keep a reference to it** for
    the duration of the call (typically on ``ctx.runtime.<feature>``), because a
    garbage-collected ``QObject`` never delivers.
    """
    bridge = WorkerBridge()

    def _disconnect() -> None:
        # Disconnecting a signal with no connections raises, so guard it — this
        # also runs when a caller passed no on_failed.
        for sig in (bridge.done, bridge.failed):
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

    def _run() -> None:
        try:
            bridge.done.emit(fn())
        except Exception as exc:      # never raise off the GUI thread
            bridge.failed.emit(str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return bridge
