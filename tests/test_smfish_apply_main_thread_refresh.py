"""Regression tests for the smFISH Apply-Global cross-thread refresh.

Pins the bug where the user had to close and reopen the app to see new
smfish_count values after pressing Apply Global Threshold:

    The worker (``apply_global_threshold_async``) runs CSV writeback
    on a daemon thread and invokes ``after_csv_cb()`` from inside that
    thread. The tab's ``_after_csv`` used to call
    ``QTimer.singleShot(0, …)`` to defer the dataset-cache refresh and
    the completion message box to the next event-loop tick — but the
    worker daemon thread has no Qt event loop, so the timer was
    created in a thread where it could never fire, and the refresh
    silently dropped on the floor. The user saw the status label flip
    to "Apply to All complete. Line/Bar plots refreshed." (that path
    DID marshal correctly via ``done_cb``) but the plots and Review
    CSV table kept showing pre-apply counts until restart.

The fix routes ``after_csv_cb`` through a new ``_WorkerBridge.after_csv``
queued signal (``Signal(object)``) so the callback runs on the GUI
thread where ``_smfish_refresh_app_cache`` and ``QMessageBox`` can do
their job. These tests pin:

1. ``_WorkerBridge`` exposes ``after_csv`` as a queued ``Signal(object)``.
2. Emitting ``after_csv`` from a worker thread runs the callback on
   the thread that owns the bridge (the GUI thread in production; the
   pytest main thread under test).
3. End-to-end: ``apply_global_threshold_async`` + the bridge wiring
   invokes ``_after_csv`` on the GUI thread.
"""

from __future__ import annotations

import threading

import pytest

# qtbot fixture from pytest-qt
pytestmark = pytest.mark.usefixtures("qtbot")


def test_worker_bridge_exposes_after_csv_signal():
    """The bridge class must declare an ``after_csv`` signal that
    carries a callable — that's the contract apply-handler relies on."""
    from review.features.smfish.tab_view import _WorkerBridge

    # The class attribute is a PySide6 ``Signal`` descriptor; the
    # instance attribute is a bound signal we can connect/emit. Both
    # are required for the apply-handler's emit-from-worker pattern.
    assert hasattr(_WorkerBridge, "after_csv"), (
        "_WorkerBridge.after_csv signal is missing — the apply-handler's "
        "cross-thread marshalling would regress to QTimer.singleShot from "
        "a non-GUI thread (silent no-op)."
    )


def test_after_csv_signal_runs_callback_on_bridge_thread(qtbot):
    """Emitting ``after_csv`` from a non-GUI thread must dispatch the
    callback on the thread that owns the bridge — that's the whole
    point of the queued connection. Without this, the apply-handler's
    widget-touching refresh runs on the worker daemon thread and
    deadlocks / no-ops."""
    from review.features.smfish.tab_view import _WorkerBridge

    bridge = _WorkerBridge(None)
    qtbot.addWidget(bridge) if hasattr(bridge, "show") else None

    main_thread = threading.current_thread()
    callback_thread: list[threading.Thread] = []
    invoked = threading.Event()

    def _record() -> None:
        callback_thread.append(threading.current_thread())
        invoked.set()

    # Wire the same handler the production code wires — a thin
    # ``lambda cb: cb()`` on the bridge thread.
    bridge.after_csv.connect(lambda cb: cb())

    def _emit_from_worker() -> None:
        # Simulate the daemon worker thread inside
        # apply_global_threshold_async._run: emit the signal carrying
        # the callback so the GUI thread runs it.
        bridge.after_csv.emit(_record)

    worker = threading.Thread(target=_emit_from_worker, daemon=True)
    worker.start()
    worker.join(timeout=2.0)

    # The signal was emitted from the worker thread, but the callback
    # is delivered via a queued connection — we need to spin the Qt
    # event loop on the main thread to drain it.
    qtbot.waitUntil(invoked.is_set, timeout=2000)

    assert callback_thread, "after_csv signal did not deliver the callback"
    assert callback_thread[0] is main_thread, (
        "after_csv callback ran on a non-main thread "
        f"({callback_thread[0].name!r}) — the queued connection isn't "
        "marshalling. Refresh-from-worker-thread would touch Qt widgets "
        "from the wrong thread and silently no-op (or crash)."
    )


def test_apply_global_threshold_after_csv_dispatches_on_gui_thread(qtbot, tmp_path):
    """End-to-end: pass ``bridge.after_csv.emit`` as the worker's
    ``after_csv_cb`` and confirm the wrapped callable runs on the GUI
    thread once the worker finishes. Locks the integration the
    production apply-handler uses."""
    from review.features.smfish.tab_view import _WorkerBridge
    from review.features.smfish.worker import apply_global_threshold_async

    bridge = _WorkerBridge(None)
    main_thread = threading.current_thread()
    saw_thread: list[threading.Thread] = []
    seen = threading.Event()

    def _refresh_proxy() -> None:
        saw_thread.append(threading.current_thread())
        seen.set()

    bridge.after_csv.connect(lambda cb: cb())

    done_evt = threading.Event()

    # No wells → the worker skips the heavy CSV-write path and still
    # runs the after_csv_cb / done_cb chain. That's enough to pin the
    # marshalling.
    thread = apply_global_threshold_async(
        out_dir=tmp_path,
        well_to_zip={},
        channel="w3cy5",
        threshold=1500.0,
        classifier=None,
        fov_tp_extractor=None,
        status_cb=lambda _s: None,
        done_cb=lambda _s: done_evt.set(),
        # Takes the shortfall the worker now reports (how many wells were
        # not written), mirroring the real callback in tab_view.
        after_csv_cb=lambda shortfall=0: bridge.after_csv.emit(_refresh_proxy),
        expected_data_dir=tmp_path,
        well_to_csv={},
    )
    # Wait for the worker thread to finish.
    thread.join(timeout=5.0)
    assert done_evt.wait(timeout=2.0), "worker done_cb never fired"

    # Drain the Qt event loop until the queued after_csv signal fires.
    qtbot.waitUntil(seen.is_set, timeout=2000)

    assert saw_thread, (
        "after_csv_cb never invoked the bridge-routed callback — the "
        "refresh would never run in production, reproducing the "
        "reopen-app-to-see-counts bug."
    )
    assert saw_thread[0] is main_thread, (
        "after_csv-routed callback ran on a non-main thread "
        f"({saw_thread[0].name!r}) — widget refresh from this thread "
        "would silently no-op (no Qt event loop on the worker daemon)."
    )
