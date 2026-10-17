"""The hover MFI lookups: the newest cell wins, and nothing outlives the window.

A cursor sweeping across an Image Table grid enters one cell after another, and
each first hover hands a mask decode and a well-index read to a background
thread. On one FIFO worker the cell under the cursor waited behind every cell
already left behind — on a network drive, ``MFI: loading…`` for as long as the
backlog took — and the worker, a non-daemon ``ThreadPoolExecutor`` thread,
drained the whole queue before the interpreter could exit. And a cursor resting
on its cell got one ToolTip event: when the lookup had not landed by then,
``loading…`` stayed on screen until the mouse moved.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np

from review.features.image_browser.image_table import _hover_mfi
from review.features.image_browser.image_table.state import ImageTableState


def _job(log, name, gate=None, started=None):
    def run():
        if started is not None:
            started.set()
        if gate is not None:
            assert gate.wait(10)
        log.append(name)
    return run


def test_a_sweep_runs_the_cell_in_progress_and_the_newest_one_only():
    """A, then B, C, D while A runs: B and C were left behind and are dropped
    (their cells told so, so hovering them again asks afresh); D runs next."""
    runner = _hover_mfi.LatestOnlyRunner("test-mfi")
    ran, dropped = [], []
    gate, started, done = threading.Event(), threading.Event(), threading.Event()
    runner.submit(_job(ran, "A", gate, started), on_dropped=lambda: dropped.append("A"))
    assert started.wait(10)
    for name in "BC":
        runner.submit(_job(ran, name), on_dropped=lambda n=name: dropped.append(n))

    def last():
        ran.append("D")
        done.set()

    runner.submit(last, on_dropped=lambda: dropped.append("D"))
    gate.set()
    assert done.wait(10)
    assert ran == ["A", "D"], f"the lookups ran {ran}; a sweep must not queue the cells it left"
    assert dropped == ["B", "C"]
    runner.shutdown()


def test_the_runner_never_holds_the_process_open_and_shutdown_drops_the_pending_job():
    runner = _hover_mfi.LatestOnlyRunner("test-mfi")
    ran, dropped = [], []
    gate, started = threading.Event(), threading.Event()
    runner.submit(_job(ran, "A", gate, started))
    assert started.wait(10)
    assert runner.thread is not None and runner.thread.daemon, (
        "a non-daemon lookup thread keeps the interpreter alive at exit")
    runner.submit(_job(ran, "B"), on_dropped=lambda: dropped.append("B"))
    runner.shutdown()
    gate.set()
    runner.thread.join(10)
    assert not runner.thread.is_alive()
    assert ran == ["A"] and dropped == ["B"]


def test_teardown_reaches_the_runner_through_the_runtime_stopper(monkeypatch):
    """``teardown_review_pane`` calls ``runtime.image_table.mfi_stop``; the first
    hover registers it."""
    from core.worker_bridge import stop_runtime_callable

    monkeypatch.setattr(_hover_mfi, "_RUNNER", None)
    monkeypatch.setattr(_hover_mfi, "_load_mask_array", lambda *a: None)
    ctx = SimpleNamespace(state=SimpleNamespace(image_table=ImageTableState()),
                          runtime=SimpleNamespace(image_table=SimpleNamespace()))
    _hover_mfi.cell_mfi(ctx, SimpleNamespace(), "gfp", "A01", "0", "1", 0, 0)
    runner = _hover_mfi._RUNNER
    assert runner is not None
    stop_runtime_callable(ctx.runtime, "image_table", "mfi_stop")
    assert runner._closed and _hover_mfi._RUNNER is None


def test_a_lookup_that_lands_while_the_cursor_rests_refreshes_the_readout(qtbot, monkeypatch):
    """The landing is delivered on the UI thread and re-shows the readout of a
    cell the cursor is still on, so "loading…" is replaced without a mouse move."""
    from PySide6.QtWidgets import QLabel

    shown: list = []
    label = QLabel()
    qtbot.addWidget(label)
    label._image_table_event_filter = SimpleNamespace(
        _hover_text=lambda lbl, x, y: _hover_mfi.cell_mfi(ctx, lbl, "gfp", "A01", "0", "1", 1, 1),
        _show_tip=lambda lbl, gp, text: shown.append((threading.current_thread(), text)))
    monkeypatch.setattr(_hover_mfi, "_cursor_rests_on", lambda lbl: lbl is label)
    monkeypatch.setattr(_hover_mfi, "_RUNNER", None)
    gate = threading.Event()

    def _mask(*_a):
        assert gate.wait(10)
        return np.full((4, 4), 4, dtype=np.int32)

    monkeypatch.setattr(_hover_mfi, "_load_mask_array", _mask)
    monkeypatch.setattr(_hover_mfi, "mfi_frame", lambda *a, **k: {4: 20.0})
    ctx = SimpleNamespace(state=SimpleNamespace(image_table=ImageTableState()))
    try:
        assert _hover_mfi.cell_mfi(ctx, label, "gfp", "A01", "0", "1", 1, 1) is _hover_mfi.MFI_PENDING
        gate.set()
        qtbot.waitUntil(lambda: bool(shown), timeout=5000)
    finally:
        gate.set()
        _hover_mfi.shutdown()
    thread, value = shown[-1]
    assert thread is threading.main_thread(), "the readout was refreshed off the UI thread"
    assert value == 20.0, f"the resting readout still shows {value!r} after the lookup landed"
