"""The progress-popup job runner: work off the GUI thread, one job at a time.

``review/core/progress_jobs.py`` is the route every Cell Gating panel edit takes.
Its promises are behavioural, so each test here runs a real job on a real thread
and watches the popup and the event loop while it does:

* the work runs on another thread and its finish on the GUI thread;
* the popup is up, application-modal, for exactly as long as the job;
* the GUI keeps running while the work does — the property the runner exists
  for, measured by a timer that must keep firing during a slow job;
* jobs never overlap, and a waiting job with a key is replaced by a newer one;
* a cancelled job applies nothing, and its popup says it is stopping until its
  thread has returned;
* a popup without Cancel cannot be dismissed.
"""

from __future__ import annotations

import threading
import time
import types

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QMainWindow  # noqa: E402

from review.core import progress_jobs  # noqa: E402


@pytest.fixture
def ctx(qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    from review.core.app_runtime import AppRuntime
    ctx = types.SimpleNamespace(window=window, runtime=AppRuntime(store={}))
    yield ctx
    progress_jobs.stop_jobs(ctx)


def _gone(popup) -> bool:
    """Closed and (once the event loop has run) deleted."""
    import shiboken6
    return not shiboken6.isValid(popup) or not popup.isVisible()


def _idle(qtbot, ctx, timeout=5000):
    qtbot.waitUntil(lambda: not progress_jobs.busy(ctx), timeout=timeout)


def test_the_work_runs_off_the_gui_thread_and_the_finish_on_it(qtbot, ctx):
    main = threading.get_ident()
    seen: dict = {}

    def _work(report):
        seen["work"] = threading.get_ident()
        report.progress(1, 2)
        return 42

    def _done(result):
        seen["done"] = threading.get_ident()
        seen["result"] = result

    progress_jobs.run_in_background(ctx, title="Job", text="working",
                                    work=_work, on_done=_done)
    _idle(qtbot, ctx)
    assert seen["result"] == 42
    assert seen["work"] != main, "the work ran on the GUI thread"
    assert seen["done"] == main, "the finish ran off the GUI thread"


def test_the_popup_is_up_and_modal_for_as_long_as_the_job(qtbot, ctx):
    release = threading.Event()
    progress_jobs.run_in_background(
        ctx, title="Applying QC", text="Re-applying…",
        work=lambda report: release.wait(5), on_done=lambda _r: None)
    popup = progress_jobs._runner(ctx).popup
    assert popup is not None and popup.isVisible()
    assert popup.windowModality() == Qt.ApplicationModal
    assert popup.windowTitle() == "Applying QC"
    assert progress_jobs.busy(ctx)
    release.set()
    _idle(qtbot, ctx)
    assert progress_jobs._runner(ctx).popup is None
    assert _gone(popup)


def test_the_gui_keeps_running_while_the_work_does(qtbot, ctx):
    """What "does not hang the GUI" means, measured: a timer on the GUI thread
    keeps firing, and the bar follows the work's reports, while a job that takes
    half a second runs. A runner that did the work on the GUI thread fires the
    timer zero times before the job ends."""
    ticks: list[float] = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    ended: list[float] = []
    values: list[int] = []

    def _work(report):
        for i in range(5):
            time.sleep(0.1)
            report.progress(i + 1, 5)
        return None

    progress_jobs.run_in_background(ctx, title="Slow", text="slow", work=_work,
                                    on_done=lambda _r: ended.append(time.monotonic()))
    popup = progress_jobs._runner(ctx).popup
    qtbot.waitUntil(lambda: popup.value() >= 3 or bool(ended), timeout=5000)
    values.append(popup.value())
    maximum = popup.maximum()
    _idle(qtbot, ctx)
    timer.stop()
    during = [t for t in ticks if t < ended[0]]
    assert len(during) >= 5, f"the GUI thread was blocked: {len(during)} ticks during the job"
    assert values[0] >= 3 and maximum == 5


def test_jobs_run_one_at_a_time_in_order(qtbot, ctx):
    log: list[str] = []
    lock = threading.Lock()

    def _job(name):
        def _work(_report):
            with lock:
                log.append(f"{name}+")
            time.sleep(0.05)
            with lock:
                log.append(f"{name}-")
        return _work

    for name in ("a", "b", "c"):
        progress_jobs.run_in_background(ctx, title=name, text=name, work=_job(name),
                                        on_done=lambda _r: None)
    _idle(qtbot, ctx)
    assert log == ["a+", "a-", "b+", "b-", "c+", "c-"]


def test_a_waiting_job_is_replaced_by_a_newer_one_with_its_key(qtbot, ctx):
    release = threading.Event()
    done: list[str] = []
    progress_jobs.run_in_background(ctx, title="first", text="",
                                    work=lambda _r: release.wait(5),
                                    on_done=lambda _r: done.append("first"))
    for name in ("old", "new"):
        progress_jobs.run_in_background(ctx, title=name, text="", work=lambda _r: None,
                                        on_done=lambda _r, n=name: done.append(n),
                                        key="qc.params")
    release.set()
    _idle(qtbot, ctx)
    assert done == ["first", "new"]


def test_a_cancelled_job_applies_nothing_and_says_it_is_stopping(qtbot, ctx):
    applied: list = []

    def _work(report):
        while not report.cancelled:
            time.sleep(0.01)
        time.sleep(0.1)            # a scan that takes a while to notice
        return "answer"

    progress_jobs.run_in_background(ctx, title="Optimize ThreshFracOn", text="",
                                    work=_work, on_done=applied.append,
                                    cancellable=True)
    runner = progress_jobs._runner(ctx)
    assert runner.popup.cancellable
    runner.popup.canceled.emit()       # what pressing Cancel emits
    stopping = runner.popup
    assert stopping is not None and stopping.isVisible()
    assert not stopping.cancellable
    assert "Cancelling" in stopping.labelText()
    assert progress_jobs.busy(ctx), "the queue moved on before the thread returned"
    _idle(qtbot, ctx)
    assert applied == [], "a cancelled job's answer was applied"


def test_a_popup_without_cancel_cannot_be_dismissed(qtbot, ctx):
    release = threading.Event()
    progress_jobs.run_in_background(ctx, title="Applying cell gating", text="",
                                    work=lambda _r: release.wait(5),
                                    on_done=lambda _r: None)
    popup = progress_jobs._runner(ctx).popup
    popup.reject()
    popup.close()
    assert popup.isVisible(), "a no-Cancel popup was dismissed while its job ran"
    release.set()
    _idle(qtbot, ctx)
    assert _gone(popup)


def test_a_failed_job_reports_and_frees_the_queue(qtbot, ctx):
    failures: list[str] = []
    after: list = []

    def _boom(_report):
        raise RuntimeError("the frames could not be read")

    progress_jobs.run_in_background(ctx, title="broken", text="", work=_boom,
                                    on_done=lambda _r: failures.append("done ran"),
                                    on_failed=failures.append)
    progress_jobs.run_in_background(ctx, title="next", text="", work=lambda _r: 1,
                                    on_done=after.append)
    _idle(qtbot, ctx)
    assert failures == ["the frames could not be read"]
    assert after == [1]


def test_a_popup_deleted_under_a_running_job_does_not_break_it(qtbot, ctx):
    """The popup's parent window can go first at app close, while the job's
    thread is still reporting."""
    release = threading.Event()
    done: list = []

    def _work(report):
        release.wait(5)
        report.progress(1, 2)
        report.stage("still going")
        return "ok"

    progress_jobs.run_in_background(ctx, title="job", text="", work=_work,
                                    on_done=done.append)
    import shiboken6
    shiboken6.delete(progress_jobs._runner(ctx).popup)
    release.set()
    _idle(qtbot, ctx)
    assert done == ["ok"]
