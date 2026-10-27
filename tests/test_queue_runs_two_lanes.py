"""A remote Analyze runs beside one local job; never two local jobs at once.

``services.job_lanes`` holds the rule, ``JobQueue.next_to_start`` applies it
and ``QueueBridge._advance`` dispatches on it. The fixtures are built so the
serial answer and the parallel answer differ: every test that expects two jobs
in flight would see one under the old one-at-a-time bridge, and every test that
expects a job held back would see it start under a lane rule with no folder
check.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal

from datasets.job_queue.controller import QueueBridge
from services.job_lanes import LOCAL, REMOTE, conflicts, folders, run_lane
from services.job_queue import JobQueue, JobSpec, JobState


class _Tool(QObject):
    jobFinished = Signal(bool, str, str)

    def __init__(self, *, suspendable: bool = False) -> None:
        super().__init__()
        self.started: list[dict] = []
        self.stops = 0
        self.suspends = 0
        self.busy = False
        self._suspended = False
        if suspendable:
            self.suspend = self._suspend
            self.was_suspended = lambda: self._suspended

    def start_from_queue(self, spec: dict) -> None:
        self.started.append(spec)

    def show_job(self, spec: dict) -> None:
        pass

    def stop(self) -> None:
        self.stops += 1

    def _suspend(self) -> None:
        self.suspends += 1
        self._suspended = True

    def is_busy(self) -> bool:
        return self.busy

    def was_cancelled(self) -> bool:
        return self.stops > 0

    def finish(self, ok: bool = True, path: str = "") -> None:
        self.jobFinished.emit(ok, "", path)


def _remote(job_id, raw, host="cluster"):
    return JobSpec(id=job_id, kind="analyze", label=job_id,
                   spec={"opts": {"raw": raw}, "remote_name": host})


def _local_an(job_id, raw):
    return JobSpec(id=job_id, kind="analyze", label=job_id, spec={"opts": {"raw": raw}})


def _conv(job_id, dest, kind="czi_import"):
    return JobSpec(id=job_id, kind=kind, label=job_id,
                   spec={"source": f"/scans/{job_id}.czi", "destination": dest})


def _validate(job_id, dataset):
    return JobSpec(id=job_id, kind="validate", label=job_id, spec={"dataset": dataset})


@pytest.fixture
def bridge(qtbot, tmp_path):
    b = QueueBridge(state_dir=tmp_path)
    b.tools = {"analyze": _Tool(suspendable=True), "czi_import": _Tool(),
               "nd2_import": _Tool(), "validate": _Tool()}
    for kind, tool in b.tools.items():
        b.register(kind, tool)
    return b


def _started(bridge, kind):
    return len(bridge.tools[kind].started)


# ── the rule, without the bridge ──────────────────────────────────────────────

def test_only_an_analyze_with_a_remote_host_is_remote():
    assert run_lane("analyze", {"opts": {}, "remote_name": "cluster"}) == REMOTE
    assert run_lane("analyze", {"opts": {}, "remote_name": None}) == LOCAL
    assert run_lane("analyze", {"opts": {}, "remote_name": "  "}) == LOCAL
    assert run_lane("czi_import", {"remote_name": "cluster"}) == LOCAL


def test_an_analysis_of_in_writes_the_dataset_root_above_it():
    assert folders("analyze", {"opts": {"raw": "/d/plate/in"}}) == ({"/d/plate/in"}, {"/d/plate"})
    assert folders("analyze", {"opts": {"raw": "/d/plate"}}) == ({"/d/plate"}, {"/d/plate"})


def test_validate_writes_what_it_checks():
    assert folders("validate", {"dataset": "/d/x"}) == ({"/d/x"}, {"/d/x"})


def test_folders_conflict_when_one_writes_inside_what_the_other_touches():
    convert = folders("czi_import", {"source": "/s/a.czi", "destination": "/d/plate"})
    assert conflicts(convert, folders("analyze", {"opts": {"raw": "/d/plate/in"}}))
    assert conflicts(folders("analyze", {"opts": {"raw": "/d/plate/in"}}), convert)
    assert not conflicts(convert, folders("analyze", {"opts": {"raw": "/d/plate2/in"}}))
    # "/d/plate2" is not inside "/d/plate": a prefix of the name is not a parent.
    assert not conflicts(convert, folders("czi_import", {"source": "/s/b.czi",
                                                         "destination": "/d/plate2"}))


def test_two_jobs_that_only_read_one_folder_do_not_conflict():
    a = folders("czi_import", {"source": "/s/a.czi", "destination": "/d/one"})
    b = folders("czi_import", {"source": "/s/a.czi", "destination": "/d/two"})
    assert not conflicts(a, b)


def test_next_to_start_respects_the_lane_and_skips_busy_kinds():
    q = JobQueue([_conv("c", "/d/c"), _remote("r", "/d/r/in")])
    q.start()
    assert q.next_to_start(LOCAL).id == "c"
    assert q.next_to_start(REMOTE).id == "r"
    assert q.next_to_start(REMOTE, unavailable={"analyze"}) is None


# ── the bridge ────────────────────────────────────────────────────────────────

def test_a_remote_analysis_runs_beside_a_local_conversion(bridge):
    bridge.queue.add(_conv("c", "/d/next"))
    bridge.queue.add(_remote("r", "/d/prev/in"))
    bridge.start()
    assert _started(bridge, "czi_import") == 1
    assert _started(bridge, "analyze") == 1
    assert bridge.running_job_ids() == ["c", "r"]


def test_two_local_jobs_never_run_together(bridge):
    bridge.queue.add(_conv("c", "/d/one"))
    bridge.queue.add(_conv("n", "/d/two", kind="nd2_import"))
    bridge.start()
    assert _started(bridge, "czi_import") == 1
    assert _started(bridge, "nd2_import") == 0
    bridge.tools["czi_import"].finish(path="/d/one")
    assert _started(bridge, "nd2_import") == 1


def test_a_remote_and_a_local_analysis_share_one_pane_so_take_turns(bridge):
    bridge.queue.add(_remote("r", "/d/a/in"))
    bridge.queue.add(_local_an("l", "/d/b/in"))
    bridge.start()
    assert bridge.running_job_ids() == ["r"]
    bridge.tools["analyze"].finish(path="/d/a/out")
    assert bridge.running_job_ids() == ["l"]


def test_a_remote_analysis_waits_for_the_conversion_above_it_writing_its_input(bridge):
    """Not linked — the input was typed — so only the folder check holds it."""
    bridge.queue.add(_conv("c", "/d/plate"))
    bridge.queue.add(_remote("r", "/d/plate/in"))
    bridge.start()
    assert bridge.running_job_ids() == ["c"]
    bridge.tools["czi_import"].finish(path="/d/plate")
    assert bridge.running_job_ids() == ["r"]


def test_a_local_job_does_not_overtake_an_earlier_job_on_its_folder(bridge):
    """The conversion that writes the remote job's input is busy behind
    another local job; the Validate below the remote job, of that same
    dataset, must not start while the remote job reads it."""
    bridge.queue.add(_remote("r", "/d/plate/in"))
    bridge.queue.add(_validate("v", "/d/plate"))
    bridge.queue.add(_conv("c", "/d/other"))
    bridge.start()
    assert bridge.running_job_ids() == ["r", "c"]
    assert _started(bridge, "validate") == 0
    bridge.tools["czi_import"].finish(path="/d/other")
    assert _started(bridge, "validate") == 0, "Validate would write what the remote run reads"
    bridge.tools["analyze"].finish(path="/d/plate/out")
    assert _started(bridge, "validate") == 1


def test_a_remote_analysis_does_not_overtake_a_waiting_conversion_of_its_input(bridge):
    """The conversion that writes the remote job's input has not started —
    the local lane is busy with another conversion — so the remote lane is
    free, and only the rule about *earlier unfinished* jobs holds it back."""
    bridge.queue.add(_conv("y", "/d/other", kind="nd2_import"))
    bridge.queue.add(_conv("c", "/d/plate"))
    bridge.queue.add(_remote("r", "/d/plate/in"))
    bridge.start()
    assert bridge.running_job_ids() == ["y"]
    bridge.tools["nd2_import"].finish(path="/d/other")
    assert bridge.running_job_ids() == ["c"]
    bridge.tools["czi_import"].finish(path="/d/plate")
    assert bridge.running_job_ids() == ["r"]


def test_the_job_started_second_can_finish_first(bridge):
    bridge.queue.add(_conv("c", "/d/next"))
    bridge.queue.add(_remote("r", "/d/prev/in"))
    bridge.start()
    bridge.tools["analyze"].finish(path="/d/prev/out")
    assert bridge.queue.get("r").state == JobState.SUCCEEDED
    assert bridge.queue.get("r").output_dir == "/d/prev/out"
    assert bridge.queue.get("c").state == JobState.RUNNING
    assert bridge.running_job_ids() == ["c"]


def test_each_finish_is_recorded_against_its_own_job(bridge):
    bridge.queue.add(_conv("c", "/d/next"))
    bridge.queue.add(_remote("r", "/d/prev/in"))
    bridge.queue.add(_conv("c2", "/d/after"))
    bridge.start()
    bridge.tools["czi_import"].finish(path="/d/next")
    assert bridge.queue.get("c").state == JobState.SUCCEEDED
    assert bridge.queue.get("c").dataset_root == "/d/next"
    assert bridge.queue.get("r").state == JobState.RUNNING
    assert bridge.running_job_ids() == ["r", "c2"]


def test_a_tool_busy_by_hand_holds_its_jobs_but_not_the_other_lane(bridge):
    bridge.tools["czi_import"].busy = True
    bridge.queue.add(_conv("c", "/d/next"))
    bridge.queue.add(_remote("r", "/d/prev/in"))
    bridge.start()
    assert bridge.running_job_ids() == ["r"]
    assert bridge.queue.get("c").state == JobState.PENDING


def test_stop_this_job_stops_only_that_one(bridge):
    bridge.queue.add(_conv("c", "/d/next"))
    bridge.queue.add(_remote("r", "/d/prev/in"))
    bridge.start()
    assert bridge.stop_job("c")
    assert bridge.tools["czi_import"].stops == 1
    assert bridge.tools["analyze"].stops == 0
    assert not bridge.queue.is_started


def test_stop_stops_every_job_in_flight(bridge):
    bridge.queue.add(_conv("c", "/d/next"))
    bridge.queue.add(_remote("r", "/d/prev/in"))
    bridge.start()
    bridge.stop_current()
    assert (bridge.tools["czi_import"].stops, bridge.tools["analyze"].stops) == (1, 1)


def test_pause_suspends_the_analysis_and_cancels_the_conversion(bridge):
    bridge.queue.add(_conv("c", "/d/next"))
    bridge.queue.add(_remote("r", "/d/prev/in"))
    bridge.start()
    assert bridge.suspend_current()
    assert bridge.tools["analyze"].suspends == 1 and bridge.tools["analyze"].stops == 0
    assert bridge.tools["czi_import"].stops == 1
    assert bridge.queue.get("r").state == JobState.SUSPENDED
    assert not bridge.queue.is_started


def test_a_restart_marks_both_running_jobs_interrupted():
    q = JobQueue([_conv("c", "/d/next"), _remote("r", "/d/prev/in")])
    q.mark_running("c")
    q.mark_running("r")
    loaded = JobQueue.from_json(q.to_json())
    assert [j.state for j in loaded.jobs] == [JobState.INTERRUPTED, JobState.INTERRUPTED]


# ── the page ──────────────────────────────────────────────────────────────────

def test_the_header_says_it_stops_both_and_a_row_stops_one(qtbot, tmp_path):
    from datasets.job_queue.controller import JobQueueController

    c = JobQueueController(state_dir=tmp_path)
    qtbot.addWidget(c.page)
    tools = {"analyze": _Tool(), "czi_import": _Tool()}
    for kind, tool in tools.items():
        c.bridge.register(kind, tool)
    c.bridge.queue.add(_conv("c", "/d/next"))
    c.bridge.queue.add(_remote("r", "/d/prev/in"))
    c.bridge.start()
    c._refresh_page()
    assert c.page._stop_btn.text() == "■  Stop both jobs"
    row = next(r for r in c.page._rows.values() if r.job_id == "c")
    stop = next(a for a in row.build_menu().actions() if a.text() == "Stop this job")
    stop.trigger()
    assert tools["czi_import"].stops == 1 and tools["analyze"].stops == 0
    tools["czi_import"].finish(ok=False)
    c._refresh_page()
    assert c.page._stop_btn.text() == "■  Stop job"


def test_a_remote_analysis_row_names_its_host():
    from services.job_kinds import describe_detail

    assert "runs on “cluster”" in describe_detail("analyze", _remote("r", "/d/p/in").spec)
    assert "runs on" not in describe_detail("analyze", _local_an("l", "/d/p/in").spec)


def test_the_real_tab_starts_a_remote_analysis_beside_a_conversion(qtbot, tmp_path, monkeypatch):
    """Through the real ``DatasetsTab`` and ``AnalyzeTab``: the registered
    runners and their real ``is_busy``. Only the two launches are replaced,
    since a real one would open an ssh connection and read a scan."""
    from analyze.tab import AnalyzeTab
    from datasets.tab import DatasetsTab

    analyze = AnalyzeTab()
    qtbot.addWidget(analyze)
    tab = DatasetsTab(analyze_controller=analyze, queue_state_dir=tmp_path,
                      on_reveal_analyze=lambda: True)
    qtbot.addWidget(tab)
    started = []
    monkeypatch.setattr(analyze, "start_from_queue", lambda spec: started.append("analyze"))
    czi = tab._controllers["czi_import"]
    monkeypatch.setattr(czi, "start_from_queue", lambda spec: started.append("czi_import"))
    bridge = tab._controllers["job_queue"].bridge
    bridge.queue.add(_conv("c", str(tmp_path / "next")))
    bridge.queue.add(_remote("r", str(tmp_path / "prev" / "in")))
    bridge.start()
    assert started == ["czi_import", "analyze"]
    assert bridge.running_job_ids() == ["c", "r"]
    bridge.pause()
    tab.shutdown()
