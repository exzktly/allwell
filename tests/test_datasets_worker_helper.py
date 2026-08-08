"""The shared Datasets worker-wait helper must fail loudly, not fail open.

``tests/_datasets_worker.run_worker_to_completion`` replaced six copies of a
helper whose two waits both returned regardless of whether they had achieved
anything. That is the failure mode being guarded here: a helper that hands back
a half-finished run does not fail *itself*, it makes some later assertion fail
somewhere else — which is exactly how a full-lane run reported
``test_dashboard_hidden_until_a_run_starts`` as a one-off flake that passed
every time it was re-run alone.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from tests._datasets_worker import run_worker_to_completion  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _SleepingWorker(QThread):
    def __init__(self, ms: int):
        super().__init__()
        self._ms = ms

    def run(self) -> None:
        self.msleep(self._ms)


class _Page:
    def __init__(self, running: bool = False):
        self._running = running


class _Ctrl:
    def __init__(self, worker, page):
        self._worker = worker
        self.page = page


def test_a_worker_that_outlives_the_timeout_is_an_error(qapp):
    worker = _SleepingWorker(3000)
    ctrl = _Ctrl(worker, _Page())
    worker.start()
    try:
        with pytest.raises(AssertionError, match="still running after"):
            run_worker_to_completion(ctrl, timeout_ms=50)
    finally:
        worker.wait(10_000)


def test_a_finished_worker_on_an_idle_page_returns(qapp):
    worker = _SleepingWorker(1)
    ctrl = _Ctrl(worker, _Page())
    worker.start()
    run_worker_to_completion(ctrl, timeout_ms=10_000)
    assert not worker.isRunning()


def test_a_page_left_running_is_an_error(qapp, monkeypatch):
    """The second half. The thread can stop while the queued ``finished`` chain
    has not reached the page — the old fixed ten turns of the event loop was a
    bet that it would, and the page's own flag is what the tests then read."""
    import tests._datasets_worker as mod

    monkeypatch.setattr(mod, "SETTLE_TIMEOUT_S", 0.2)
    worker = _SleepingWorker(1)
    ctrl = _Ctrl(worker, _Page(running=True))
    worker.start()
    with pytest.raises(AssertionError, match="_running is still"):
        run_worker_to_completion(ctrl, timeout_ms=10_000)


def test_no_worker_at_all_is_an_error(qapp):
    """Waiting on a run that never started would otherwise pass vacuously."""
    with pytest.raises(AssertionError, match="no worker"):
        run_worker_to_completion(_Ctrl(None, _Page()))
