"""A run whose event queue fills keeps every well's terminal line, and says truthfully what it dropped.

Reported (tracker C11): when the GUI fell behind a remote run's stream, ``PipelineRunner._enqueue`` dropped
whatever arrived next — "Well X — done." included — so the well stayed "processing" for the rest of the run and
the headline never reached its total. The one overflow warning was tried once, could itself be dropped while the
queue was still full, and said the dropped lines were "still captured in the help-drawer log tab", which they
were not.

Driven through the real ``PipelineRunner._enqueue`` with a small real ``RunEventQueue`` and a reader that drains
it late, the way ``AnalyzeTab._poll_log`` does when the GUI is busy; the assertions are on what came out of the
queue.
"""

from __future__ import annotations

import queue
import threading
import time

from services.pipeline_runner import PipelineRunner, RunEventQueue

DONE = "2026-05-27 10:30:45  INFO      Well A01 — done.\n"
SKIP = "SKIP well B02 — output already exists. Use --force to reprocess.\n"
PARTIAL = "2026-05-27 10:30:45  WARNING   Well C03 — PARTIAL: 2 of 9 FOV-group(s) FAILED (x). The per-well\n"


def _runner(maxsize: int) -> PipelineRunner:
    r = PipelineRunner()
    r.log_q = RunEventQueue(maxsize=maxsize)
    r.log_q._on_error = r._latch_error_state
    return r


def _write_then_drain_late(r: PipelineRunner, events) -> list:
    """Write *events* on a thread of their own while nothing reads, then read everything."""
    writer = threading.Thread(target=lambda: [r._enqueue(e) for e in events], daemon=True)
    writer.start()
    time.sleep(0.3)                           # the GUI is busy: nothing drains meanwhile
    out = []
    while writer.is_alive() or not r.log_q.empty():
        try:
            out.append(r.log_q.get(timeout=1.0))
        except queue.Empty:
            break
    writer.join(2.0)
    assert not writer.is_alive(), "the writer never finished"
    return out


def test_a_full_queue_drops_noise_but_never_a_well_s_terminal_line():
    r = _runner(maxsize=4)
    events = [("line", f"noise {i}\n") for i in range(10)]
    events[5:5] = [("line", DONE), ("line", SKIP), ("line", PARTIAL)]
    events.append(("finished", None))
    got = [p for _k, p in _write_then_drain_late(r, events)]
    for line in (DONE, SKIP, PARTIAL):
        assert line in got, f"{line!r} was dropped; got {got}"
    assert got[-1] is None, "the run's end must still be the last event"


def test_the_drop_count_is_said_before_the_run_ends_and_is_true():
    r = _runner(maxsize=4)
    events = [("line", f"noise {i}\n") for i in range(10)] + [("finished", None)]
    got = [p for _k, p in _write_then_drain_late(r, events)]
    noise = [p for p in got if isinstance(p, str) and p.startswith("noise")]
    warnings = [p for p in got if isinstance(p, str) and "were dropped" in p]
    dropped = 10 - len(noise)
    assert dropped > 0, "the fixture never filled the queue"
    said = sum(int(w.split("[warn] ")[1].split(" ")[0]) for w in warnings)
    assert said == dropped, f"said {said} dropped, actually dropped {dropped}: {warnings}"
    assert got.index(warnings[-1]) < got.index(None), "the count must reach the log before the run ends"
    assert all("help-drawer" not in w for w in warnings), "the lines are not kept in the help drawer"
