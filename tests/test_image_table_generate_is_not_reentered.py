"""Nothing a Generate's progress bar lets through runs inside the Generate.

A Generate repaints its progress bar by pumping the event loop with
``ExcludeUserInputEvents`` (``image_table/_load_progress``). That keeps clicks
out and nothing else: timers and queued signals are delivered mid-Generate, with
the grid half rebuilt. The phone refit's timer calls ``image_table_generate``,
so a rotation during a long Generate drew a second table into the grid the first
was still filling; a finished run's deferred dataset load
(``AllWellApp._load_in_review_later``) could reset the dataset under one.

These tests drive the real entry points in that order — a Generate pumping
through the real ``LoadProgress`` while a timer armed before it fires — and
record what ran inside what.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def _generate_that_pumps(log, inside, *, ticks=40):
    """A stand-in for the Generate's body: marks the span and pumps through the real progress bar."""
    from PySide6.QtWidgets import QLabel, QProgressBar

    from review.features.image_browser.image_table import _load_progress

    def body(ctx, *, retry_missing):
        log.append(("start", retry_missing))
        inside.append(True)
        bar, label = QProgressBar(), QLabel()
        clock = iter(x * 0.1 for x in range(10_000))
        progress = _load_progress.LoadProgress(bar, label, clock=lambda: next(clock))
        progress.phase("Drawing images", ticks)
        import time
        for _ in range(ticks):
            time.sleep(0.01)
            progress.tick()
        progress.finish()
        inside.pop()
        log.append(("end", retry_missing))
    return body


def test_a_generate_asked_for_mid_generate_runs_once_after_it(qapp, qtbot, monkeypatch):
    from PySide6.QtCore import QTimer

    from review.features.image_browser.image_table import controller

    log: list = []
    inside: list = []
    monkeypatch.setattr(controller.image_table_generate, "unguarded", _generate_that_pumps(log, inside))
    ctx = object()
    # Three asks arrive while the first Generate draws (a refit timer firing on
    # each relayout); one of them is the Generate button's, which re-walks.
    QTimer.singleShot(0, lambda: controller.image_table_generate(ctx))
    QTimer.singleShot(0, lambda: controller.image_table_generate(ctx, retry_missing=True))
    QTimer.singleShot(0, lambda: controller.image_table_generate(ctx))
    controller.image_table_generate(ctx)
    qtbot.waitUntil(lambda: len(log) >= 4, timeout=5_000)
    qtbot.wait(100)
    assert log == [("start", False), ("end", False), ("start", True), ("end", True)], (
        f"a Generate ran inside another, or the asks were not merged into one: {log}")


def test_a_dataset_load_handed_over_mid_generate_waits_for_it(qapp, qtbot, monkeypatch):
    import allwell
    from review.features.image_browser.image_table import controller

    log: list = []
    inside: list = []
    monkeypatch.setattr(controller.image_table_generate, "unguarded", _generate_that_pumps(log, inside))

    class _Review:
        def _load_path(self, path):
            log.append(("load", bool(inside)))

    win = allwell.AllWellApp.__new__(allwell.AllWellApp)
    win._review = _Review()
    win._load_in_review_later("/a/finished/run", delay_ms=0)
    controller.image_table_generate(object())
    qtbot.waitUntil(lambda: any(e[0] == "load" for e in log), timeout=5_000)
    assert log == [("start", False), ("end", False), ("load", False)], (
        f"the dataset load ran inside the Generate: {log}")


def test_with_nothing_busy_a_load_runs_on_its_own_timer(qapp, qtbot):
    import allwell

    seen: list = []

    class _Review:
        def _load_path(self, path):
            seen.append(path)

    win = allwell.AllWellApp.__new__(allwell.AllWellApp)
    win._review = _Review()
    win._load_in_review_later("/x", delay_ms=0)
    qtbot.waitUntil(lambda: seen == ["/x"], timeout=5_000)
