"""Watch folder: when a folder has settled, and what the queue does with that.

The settle rule is tested on hand-built snapshots and a hand-driven clock, so
each case says exactly what changed when. The worker and the controller are
then run for real against a temporary folder — with a quiet period of a
fraction of a second — and the queue tests drive a real ``QueueBridge`` with
the real Watch folder controller registered, so the job's reported folder, its
lane and the link it feeds are the app's own.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal

from datasets.job_queue.controller import QueueBridge
from datasets.watch_folder.controller import WatchFolderController
from datasets.watch_folder.watch_folder_compute import (
    MISSING,
    SETTLED,
    SETTLING,
    WAITING,
    SettleTracker,
    snapshot,
)
from services.job_kinds import CHAIN_KIND, describe_detail
from services.job_lanes import LOCAL, WATCH, folders, run_lane
from services.job_queue import ChainLink, JobSpec, JobState


# ── the settle rule ───────────────────────────────────────────────────────────

A = {"a.nd2": (10, 1)}
A_GROWN = {"a.nd2": (20, 2)}
AB = {"a.nd2": (20, 2), "b.nd2": (5, 3)}


def test_a_watch_waits_for_a_change_before_counting():
    t = SettleTracker(60, require_change=True)
    assert t.observe(A, 0).phase == WAITING
    # A folder already holding files and not changing never settles: those
    # files are the previous acquisition, not this one.
    assert t.observe(A, 500).phase == WAITING
    assert t.observe(A_GROWN, 510).phase == SETTLING
    assert t.observe(A_GROWN, 569).phase == SETTLING
    status = t.observe(A_GROWN, 570)
    assert status.phase == SETTLED
    assert status.quiet_for == 60
    assert status.changes == 1


def test_every_change_restarts_the_countdown():
    t = SettleTracker(60)
    t.observe({}, 0)
    t.observe(A, 10)
    t.observe(A_GROWN, 60)          # growing — a file still being written
    assert t.observe(A_GROWN, 110).phase == SETTLING   # 50 s quiet, not 100
    t.observe(AB, 115)              # a second file arrives
    assert t.observe(AB, 170).phase == SETTLING
    assert t.observe(AB, 175).phase == SETTLED


def test_a_touched_file_of_the_same_size_is_a_change():
    t = SettleTracker(60)
    t.observe({}, 0)
    t.observe({"a.nd2": (10, 1)}, 1)
    t.observe({"a.nd2": (10, 9)}, 50)     # rewritten in place, same size
    assert t.observe({"a.nd2": (10, 9)}, 100).phase == SETTLING
    assert t.observe({"a.nd2": (10, 9)}, 110).phase == SETTLED


def test_without_require_change_a_complete_quiet_folder_settles():
    t = SettleTracker(60, require_change=False)
    assert t.observe(A, 0).phase == SETTLING
    assert t.observe(A, 60).phase == SETTLED


def test_an_empty_folder_never_settles():
    t = SettleTracker(60, require_change=False)
    t.observe({}, 0)
    assert t.observe({}, 1000).phase == SETTLING
    t2 = SettleTracker(60)
    t2.observe(A, 0)
    t2.observe({}, 1)               # emptied: a change, but nothing to process
    assert t2.observe({}, 1000).phase == SETTLING


def test_a_folder_that_does_not_exist_yet_is_waited_for():
    t = SettleTracker(60)
    assert t.observe(None, 0).phase == MISSING
    assert t.observe(None, 100).phase == MISSING
    assert t.observe(A, 200).phase == SETTLING     # its appearance is a change
    assert t.observe(A, 260).phase == SETTLED


def test_settled_is_final():
    t = SettleTracker(1, require_change=False)
    t.observe(A, 0)
    assert t.observe(A, 1).phase == SETTLED
    assert t.observe(AB, 2).phase == SETTLED


def test_the_status_sentence_counts_down():
    t = SettleTracker(300)
    t.observe({}, 0)
    status = t.observe(AB, 10)
    status = t.observe(AB, 100)
    assert status.sentence() == "Receiving files — 2 files, quiet for 1:30 of 5:00."
    assert status.remaining == 210


def test_the_snapshot_leaves_out_what_a_person_browsing_creates(tmp_path):
    (tmp_path / "plate.nd2").write_bytes(b"x" * 7)
    (tmp_path / ".DS_Store").write_bytes(b"finder")
    (tmp_path / "._plate.nd2").write_bytes(b"appledouble")
    (tmp_path / "Thumbs.db").write_bytes(b"win")
    (tmp_path / "~$notes.docx").write_bytes(b"lock")
    hidden = tmp_path / ".cache"
    hidden.mkdir()
    (hidden / "x.tif").write_bytes(b"x")
    sub = tmp_path / "pos1"
    sub.mkdir()
    (sub / "img.tif").write_bytes(b"xyz")
    snap = snapshot(tmp_path)
    assert sorted(snap) == ["plate.nd2", "pos1/img.tif"]
    assert snap["plate.nd2"][0] == 7
    assert snapshot(tmp_path / "not-there") is None


# ── the kind's place in the queue ─────────────────────────────────────────────

def test_a_watch_has_a_lane_of_its_own_and_writes_nothing():
    spec = {"folder": "/scope/run7"}
    assert run_lane("watch_folder", spec) == WATCH
    assert folders("watch_folder", spec) == ({"/scope/run7"}, set())


def test_a_watch_row_says_how_it_decides():
    assert describe_detail("watch_folder", {"folder": "/scope/run7", "quiet_minutes": 2.5,
                                            "require_change": False}) == (
        "/scope/run7 · settled after 2.5 min with no change · counts what is already there")


# ── the real controller, on a real folder ─────────────────────────────────────

def _fast(spec: dict) -> dict:
    """A spec that settles in a fraction of a second, polled every 20 ms."""
    return {"quiet_minutes": 0.3 / 60, "poll_seconds": 0.02, **spec}


def _stop(controller, qtbot) -> None:
    """Stop the worker and let its last queued signals land while the page
    still exists — the test reaper deletes the page straight afterwards."""
    controller.shutdown()
    qtbot.wait(150)


def test_the_controller_reports_the_folder_once_it_settles(qtbot, tmp_path):
    controller = WatchFolderController()
    try:
        with qtbot.waitSignal(controller.jobFinished, timeout=10000) as blocker:
            controller.start_from_queue(_fast({"folder": str(tmp_path),
                                               "require_change": True}))
            qtbot.wait(100)
            (tmp_path / "plate.nd2").write_bytes(b"x" * 100)
        ok, message, path = blocker.args
        assert ok, message
        assert path == str(tmp_path)
        assert message.startswith("Settled — 1 file")
        assert controller.page.status_text().startswith("✓ Settled")
    finally:
        _stop(controller, qtbot)


def test_a_folder_still_growing_does_not_settle(qtbot, tmp_path):
    controller = WatchFolderController()
    finished: list[tuple] = []
    controller.jobFinished.connect(lambda *a: finished.append(a))
    target = tmp_path / "plate.nd2"
    try:
        controller.start_from_queue(_fast({"folder": str(tmp_path)}))
        began = time.monotonic()
        # Grow the file more often than the 0.3 s quiet period for 1.2 s.
        while time.monotonic() - began < 1.2:
            with open(target, "ab") as fh:
                fh.write(b"x")
            qtbot.wait(100)
        assert finished == [], "settled while a file was still being written"
        qtbot.waitUntil(lambda: bool(finished), timeout=5000)
        assert finished[0][0] is True
    finally:
        _stop(controller, qtbot)


def test_stopping_a_watch_reads_as_a_cancellation(qtbot, tmp_path):
    controller = WatchFolderController()
    try:
        with qtbot.waitSignal(controller.jobFinished, timeout=5000) as blocker:
            controller.start_from_queue(_fast({"folder": str(tmp_path)}))
            qtbot.wait(100)
            controller.stop()
        ok, message, path = blocker.args
        assert not ok and path == ""
        assert message.startswith("Cancelled")
    finally:
        _stop(controller, qtbot)


def test_a_watch_gives_up_when_asked_to(qtbot, tmp_path):
    controller = WatchFolderController()
    try:
        with qtbot.waitSignal(controller.jobFinished, timeout=5000) as blocker:
            controller.start_from_queue(_fast({"folder": str(tmp_path),
                                               "give_up_hours": 0.3 / 3600}))
        ok, message, _path = blocker.args
        assert not ok
        assert message.startswith("Gave up after")
    finally:
        _stop(controller, qtbot)


def test_the_page_round_trips_a_queued_spec(qtbot, tmp_path):
    controller = WatchFolderController()
    try:
        spec = {"folder": str(tmp_path), "quiet_minutes": 12.5,
                "require_change": False, "give_up_hours": 6.0}
        controller.show_job(spec)
        assert controller.collect_spec() == spec
    finally:
        _stop(controller, qtbot)


# ── in the queue ──────────────────────────────────────────────────────────────

class _Tool(QObject):
    jobFinished = Signal(bool, str, str)

    def __init__(self) -> None:
        super().__init__()
        self.started: list[dict] = []

    def start_from_queue(self, spec: dict) -> None:
        self.started.append(spec)

    def show_job(self, spec: dict) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_busy(self) -> bool:
        return False


@pytest.fixture
def queue(qtbot, tmp_path):
    bridge = QueueBridge(state_dir=tmp_path / "state")
    watch = WatchFolderController()
    nd2, czi = _Tool(), _Tool()
    bridge.register("watch_folder", watch)
    bridge.register("nd2_import", nd2)
    bridge.register("czi_import", czi)
    yield bridge, watch, nd2, czi
    _stop(watch, qtbot)


def test_a_watch_releases_the_conversion_linked_to_it(queue, qtbot, tmp_path):
    bridge, watch, nd2, _czi = queue
    scope = tmp_path / "run7"
    scope.mkdir()
    watch_job = JobSpec(id="w", kind="watch_folder", label="w",
                        spec=_fast({"folder": str(scope)}))
    convert = JobSpec(id="c", kind="nd2_import", label="c",
                      spec={"source": "/old/run6.nd2", "destination": str(tmp_path / "out")})
    bridge.add_job(watch_job)
    assert bridge.add_job(convert, [ChainLink("w", "source", CHAIN_KIND)]) == []
    bridge.start()
    qtbot.wait(100)
    assert nd2.started == [], "the conversion started before the folder settled"
    assert bridge.queue.get("w").state == JobState.RUNNING
    (scope / "run7.nd2").write_bytes(b"x" * 10)
    qtbot.waitUntil(lambda: bool(nd2.started), timeout=10000)
    assert bridge.queue.get("w").state == JobState.SUCCEEDED
    # The converter is handed the watched folder; it chooses the file itself
    # (datasets.core.watched_source), so nothing here guesses at a name.
    assert nd2.started[0]["source"] == str(scope)


def test_a_running_watch_does_not_hold_up_local_work(queue, qtbot, tmp_path):
    bridge, _watch, _nd2, czi = queue
    scope = tmp_path / "run7"
    scope.mkdir()
    bridge.add_job(JobSpec(id="w", kind="watch_folder", label="w",
                           spec=_fast({"folder": str(scope)})))
    bridge.add_job(JobSpec(id="other", kind="czi_import", label="other",
                           spec={"source": "/elsewhere/p.czi",
                                 "destination": str(tmp_path / "p_out")}))
    bridge.start()
    qtbot.waitUntil(lambda: bool(czi.started), timeout=2000)
    assert bridge.queue.get("w").state == JobState.RUNNING
    assert run_lane("czi_import", {}) == LOCAL


def test_only_a_watch_can_feed_a_converter(queue):
    bridge, *_ = queue
    bridge.add_job(JobSpec(id="v", kind="czi_import", label="v",
                           spec={"source": "/a.czi", "destination": "/d1"}))
    bridge.add_job(JobSpec(id="w", kind="watch_folder", label="w",
                           spec={"folder": "/scope"}))
    bridge.add_job(JobSpec(id="c", kind="nd2_import", label="c",
                           spec={"source": "/b.nd2", "destination": "/d2"}))
    assert bridge.set_link("c", "source", "v") is False
    assert bridge.queue.get("c").links() == []
    assert bridge.set_chain("c", "w") is True
    assert [(link.source, link.target) for link in bridge.queue.get("c").links()] == [
        ("w", "source")]


# ── review findings ───────────────────────────────────────────────────────────

def test_pausing_the_queue_keeps_a_watch_and_its_chain(queue, qtbot, tmp_path):
    """Pause fell back to Stop for a watch, cancelling it and blocking every
    job chained after it."""
    bridge, watch, nd2, _czi = queue
    scope = tmp_path / "run7"
    scope.mkdir()
    bridge.add_job(JobSpec(id="w", kind="watch_folder", label="w",
                           spec=_fast({"folder": str(scope), "quiet_minutes": 10.0})))
    bridge.add_job(JobSpec(id="c", kind="nd2_import", label="c",
                           spec={"source": "/old/a.nd2", "destination": str(tmp_path / "o")}),
                   [ChainLink("w", "source", CHAIN_KIND)])
    bridge.start()
    qtbot.waitUntil(watch.is_busy, timeout=2000)
    assert bridge.suspend_current() is True
    qtbot.waitUntil(lambda: not watch.is_busy(), timeout=3000)
    qtbot.wait(100)
    assert bridge.queue.get("w").state == JobState.SUSPENDED
    assert "watches the folder again" in bridge.queue.get("w").message
    assert bridge.queue.get("c").state == JobState.PENDING
    assert nd2.started == []
    bridge.start()
    qtbot.waitUntil(lambda: bridge.queue.get("w").state == JobState.RUNNING, timeout=2000)


def test_a_running_watch_shows_its_countdown_on_its_row(queue, qtbot, tmp_path):
    bridge, watch, _nd2, _czi = queue
    scope = tmp_path / "run7"
    scope.mkdir()
    (scope / "old.nd2").write_bytes(b"x")
    bridge.add_job(JobSpec(id="w", kind="watch_folder", label="w",
                           spec=_fast({"folder": str(scope), "quiet_minutes": 10.0})))
    bridge.start()
    qtbot.waitUntil(lambda: bridge.queue.get("w").message.startswith("Waiting for new files"),
                    timeout=3000)
    assert bridge.queue.get("w").message == "Waiting for new files (1 file there now)…"
