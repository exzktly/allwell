"""The shared worker bridge's reporting channel.

``run_off_thread`` delivers a worker's *result* on the GUI thread. Long jobs also
need to say something while they run, and a worker thread cannot touch a widget
— it has no event loop, and ``QTimer.singleShot(0, …)`` from one silently drops
the call. So the bridge grew two more queued signals and hands the worker a
reporter with no widget access at all.

The compatibility guarantee tested here matters as much as the feature: a caller
that wants no reporting still has its ``fn`` called with **no arguments**. Both
existing callers rely on that, and a signature change that "obviously" works is
exactly the kind that breaks a callable defined three modules away.

``qtbot`` only, no app fixture — fast lane.
"""

from __future__ import annotations

from core.worker_bridge import run_off_thread


def test_fn_is_called_with_no_arguments_when_nothing_is_reported(qtbot):
    seen = []
    run_off_thread(lambda: seen.append("called") or "ok",
                   on_done=lambda r: seen.append(r))
    qtbot.waitUntil(lambda: "ok" in seen, timeout=3000)


def test_a_reporter_is_passed_when_progress_is_wanted(qtbot):
    got: list[tuple[int, int]] = []
    done: list[object] = []

    def _work(reporter):
        reporter.progress(1, 4)
        reporter.progress(4, 4)
        return "finished"

    run_off_thread(_work, on_done=done.append,
                   on_progress=lambda d, t: got.append((d, t)))
    qtbot.waitUntil(lambda: done == ["finished"], timeout=3000)
    assert got == [(1, 4), (4, 4)]


def test_stage_names_arrive_as_strings(qtbot):
    stages: list[str] = []
    done: list[object] = []

    def _work(reporter):
        reporter.stage("loading")
        reporter.stage("working")
        return None

    run_off_thread(_work, on_done=done.append, on_stage=stages.append)
    qtbot.waitUntil(lambda: len(done) == 1, timeout=3000)
    assert stages == ["loading", "working"]


def test_the_reporter_exposes_no_way_to_reach_the_bridge(qtbot):
    """A worker is given two methods, not the QObject. Handing it the bridge
    would let it connect to, read, or outlive the call it belongs to."""
    seen: list[list[str]] = []
    done: list[object] = []

    def _work(reporter):
        seen.append([n for n in dir(reporter) if not n.startswith("_")])
        return None

    run_off_thread(_work, on_done=done.append, on_progress=lambda d, t: None)
    qtbot.waitUntil(lambda: len(done) == 1, timeout=3000)
    assert sorted(seen[0]) == ["progress", "stage"]


def test_reporting_does_not_break_the_one_shot_disconnect(qtbot):
    """The bridge still drops every connection after the first outcome — the
    leak guard that made it one-shot in the first place."""
    done: list[object] = []
    late: list[tuple[int, int]] = []
    bridge = run_off_thread(lambda reporter: "once", on_done=done.append,
                            on_progress=lambda d, t: late.append((d, t)))
    qtbot.waitUntil(lambda: done == ["once"], timeout=3000)

    bridge.done.emit("twice")
    bridge.progress.emit(9, 9)
    qtbot.wait(50)
    assert done == ["once"], "done stayed connected after firing"
    assert late == [], "progress stayed connected after the job finished"


def test_a_failure_still_reaches_on_failed_with_a_reporter_attached(qtbot):
    def _boom(reporter):
        reporter.stage("about to fail")
        raise RuntimeError("nope")

    failed: list[str] = []
    stages: list[str] = []
    run_off_thread(_boom, on_done=lambda r: None, on_failed=failed.append,
                   on_stage=stages.append)
    qtbot.waitUntil(lambda: failed == ["nope"], timeout=3000)
    assert stages == ["about to fail"]
