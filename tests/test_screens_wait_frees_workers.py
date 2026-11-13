"""``tests/_screens.pump`` waits without starving the app's worker threads.

The full-app render tests (``tests/_page_render_case.py``, ``tests/_page_tour.py``) pump the event loop and
then measure a page. ``QTest.qWait`` keeps the GIL for its whole wait, so a worker thread beside it got about
0.3% of its time: the Nanowells page's Rare responders analysis, 1.9 s on its own, took 69–88 s, and a page
measured after a wait could be measured before its data arrived. ``wait_events`` sleeps in Python between
event-loop slices instead.
"""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PySide6")


def _work_alone_s(n: int) -> float:
    start = time.perf_counter()
    total = 0
    for i in range(n):
        total += i
    return time.perf_counter() - start


def test_a_worker_finishes_its_work_while_the_harness_waits(qapp):
    """The worker must finish *during* the wait, well before it ends. ``QTest.qWait`` holds the GIL for its
    whole call, so beside it the worker runs only once the wait has returned: it then finishes a few
    milliseconds after the pump, which a check of "finished by the time we look" cannot tell apart."""
    from tests._screens import pump

    n = 300_000
    alone = _work_alone_s(n)                                  # about ten milliseconds on its own
    finished_at = []

    def work() -> None:
        _work_alone_s(n)
        finished_at.append(time.monotonic())

    wait_ms = max(400, int(alone * 1000 * 20))                # twenty times what the work needs alone
    thread = threading.Thread(target=work, daemon=True)
    start = time.monotonic()
    thread.start()
    pump(qapp, n=1, wait_ms=wait_ms)
    thread.join(timeout=60)
    assert finished_at, "the worker never finished"
    took = finished_at[0] - start
    assert took < wait_ms / 1000 / 2, (f"a {alone * 1000:.0f} ms job took {took * 1000:.0f} ms beside a "
                                        f"{wait_ms} ms pump: the wait held the GIL")


def test_the_wait_still_runs_timers_and_lasts_as_long_as_asked(qapp):
    from PySide6.QtCore import QTimer

    from tests._screens import wait_events

    fired = []
    QTimer.singleShot(50, lambda: fired.append(True))
    start = time.monotonic()
    wait_events(qapp, 250)
    assert fired == [True]
    assert time.monotonic() - start >= 0.25
