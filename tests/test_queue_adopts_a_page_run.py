"""A run started on a tool's own page joins the queue as a running job.

Jobs queued after it then wait for it — the same lane, the same folders, or a
link to its output — and run when it finishes, instead of the queue merely
skipping the busy tool. The tests that matter go through a real tool
controller's ``_start_worker`` (the one seam every Datasets run starts
through) and the real ``DatasetsTab`` wiring; the guard at the bottom keeps
that seam the only one.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal

from tests._repo_scan import parse_source, python_sources, rel

from datasets.job_queue.controller import QueueBridge
from services.job_queue import JobQueue, JobSpec, JobState

REPO = Path(__file__).resolve().parent.parent


class _Worker:
    """Stands in for a QThread worker: started, running until told otherwise."""

    def __init__(self) -> None:
        self.running = False

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running


@pytest.fixture
def tab(qtbot, tmp_path):
    from datasets.tab import DatasetsTab

    t = DatasetsTab(analyze_controller=None, queue_state_dir=tmp_path)
    qtbot.addWidget(t)
    yield t
    for c in t._controllers.values():
        w = getattr(c, "_worker", None)
        if isinstance(w, _Worker):
            c._worker = None
    t.shutdown()


def _run_validate_by_hand(tab, dataset: str) -> None:
    validate = tab._controllers["validate"]
    validate.page.set_dataset_path(dataset)
    validate._start_worker(_Worker())


def _conv(job_id, dest):
    return JobSpec(id=job_id, kind="czi_import", label=job_id,
                   spec={"source": f"/scans/{job_id}.czi", "destination": dest})


def test_a_run_started_on_its_page_joins_the_queue_running(tab):
    bridge = tab._controllers["job_queue"].bridge
    _run_validate_by_hand(tab, "/d/plate")
    [job] = bridge.queue.jobs
    assert (job.kind, job.state, job.adopted) == ("validate", JobState.RUNNING, True)
    assert job.spec == {"dataset": "/d/plate", "apply_repair": False}
    assert job.label == "Validate — plate"
    assert job.message == "started on its own page"
    assert bridge.running_job_ids() == [job.id]


def test_it_goes_above_the_waiting_jobs_and_below_the_finished_ones(tab):
    bridge = tab._controllers["job_queue"].bridge
    bridge.queue.add(_conv("done", "/d/done"))
    bridge.queue.mark_running("done")
    bridge.queue.mark_finished("done", ok=True, produced={"dataset_root": "/d/done"})
    bridge.queue.add(_conv("waiting", "/d/next"))
    _run_validate_by_hand(tab, "/d/plate")
    assert [j.id for j in bridge.queue.jobs][0] == "done"
    assert bridge.queue.jobs[1].adopted
    assert bridge.queue.jobs[2].id == "waiting"


def test_a_job_queued_behind_it_runs_when_it_finishes(tab, monkeypatch):
    """The same lane: two local jobs never run together, so the conversion
    queued after the hand-started Validate waits for it."""
    bridge = tab._controllers["job_queue"].bridge
    czi = tab._controllers["czi_import"]
    started = []
    monkeypatch.setattr(czi, "start_from_queue", lambda spec: started.append(spec))
    _run_validate_by_hand(tab, "/d/plate")
    bridge.add_job(_conv("next", "/d/next"))
    bridge.start()
    assert started == [], "a local job started beside a local run"
    validate = tab._controllers["validate"]
    validate._worker = None
    validate.jobFinished.emit(True, "", "/d/plate")
    adopted = next(j for j in bridge.queue.jobs if j.adopted)
    assert adopted.state == JobState.SUCCEEDED
    assert adopted.dataset_root == "/d/plate"
    assert [s["destination"] for s in started] == ["/d/next"]
    bridge.pause()


def test_a_job_linked_to_it_receives_its_output(tab):
    bridge = tab._controllers["job_queue"].bridge
    _run_validate_by_hand(tab, "/d/plate")
    adopted = bridge.queue.jobs[0]
    bridge.add_job(JobSpec(id="sub", kind="subset", label="sub",
                           spec={"source": "/planned", "destination": "/d/sub"}))
    assert bridge.set_link("sub", "source", adopted.id)
    validate = tab._controllers["validate"]
    validate._worker = None
    validate.jobFinished.emit(True, "", "/d/plate-real")
    assert bridge.queue.materialise("sub")["source"] == "/d/plate-real"


def test_a_failed_run_blocks_what_reads_from_it(tab):
    bridge = tab._controllers["job_queue"].bridge
    _run_validate_by_hand(tab, "/d/plate")
    adopted = bridge.queue.jobs[0]
    bridge.add_job(JobSpec(id="sub", kind="subset", label="sub",
                           spec={"source": "/planned", "destination": "/d/sub"}))
    bridge.set_link("sub", "source", adopted.id)
    validate = tab._controllers["validate"]
    validate._worker = None
    validate.jobFinished.emit(False, "unreadable", "")
    assert adopted.state == JobState.FAILED
    assert bridge.queue.get("sub").state == JobState.BLOCKED


class _QueueTool(QObject):
    jobFinished = Signal(bool, str, str)
    runStarted = Signal(dict)

    def start_from_queue(self, spec: dict) -> None:
        self.runStarted.emit(spec)    # what every real tool does as it starts

    def show_job(self, spec: dict) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_busy(self) -> bool:
        return False


def test_a_run_the_queue_started_is_not_taken_in_twice(qtbot, tmp_path):
    bridge = QueueBridge(state_dir=tmp_path)
    bridge.register("czi_import", _QueueTool())
    bridge.queue.add(_conv("c", "/d/c"))
    bridge.start()
    assert [j.id for j in bridge.queue.jobs] == ["c"]
    assert bridge.running_job_ids() == ["c"]


def test_after_a_restart_the_queue_does_not_resume_it_itself():
    """Its page owns resuming it — Analyze offers the run back from its own
    banner, and the queue resuming it too would run one plate twice."""
    q = JobQueue()
    q.adopt(JobSpec(id="m", kind="analyze", label="m", spec={"opts": {"raw": "/d/p"}}))
    q.add(JobSpec(id="v", kind="validate", label="v", spec={}))
    q.set_link("v", "dataset", "m")
    loaded = JobQueue.from_json(q.to_json())
    assert loaded.get("m").state == JobState.CANCELLED
    assert "resume it there" in loaded.get("m").message
    assert loaded.get("v").state == JobState.BLOCKED
    loaded.start()
    assert loaded.next_runnable() is None


def test_a_queue_job_interrupted_by_a_restart_is_still_resumable():
    q = JobQueue([JobSpec(id="j", kind="validate", label="j", spec={})])
    q.mark_running("j")
    assert JobQueue.from_json(q.to_json()).get("j").state == JobState.INTERRUPTED


def test_analyze_announces_the_run_it_starts(qtbot, tmp_path, monkeypatch):
    """Through the real ``start_run``, every refusal before it passed, with
    only the runner's launch replaced."""
    from analyze.tab import AnalyzeTab
    from services.run_spec import RunSpec

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    plate = tmp_path / "plate"
    plate.mkdir()
    (plate / "A01_w1DAPI_s1_t1.tif").write_bytes(b"")
    monkeypatch.setattr(tab._runner, "start", lambda *a, **k: None)
    got = []
    tab.runStarted.connect(got.append)
    error = tab.start_run(RunSpec(opts={"raw": str(plate)}), pipeline=tmp_path / "pm.py")
    assert error is None, error
    assert [s["opts"]["raw"] for s in got] == [str(plate)]


# ── the guard ─────────────────────────────────────────────────────────────────

#: Where a Datasets controller may start a worker without ``_start_worker``:
#: the seam itself, and a worker that is not a run — it emits no
#: ``jobFinished`` and the queue has nothing to wait for.
NOT_A_RUN = {
    ("datasets/core/tool_controller.py", "_start_worker"):
        "the seam itself: it starts the worker and announces the run",
    ("datasets/concatenate/controller.py", "_start_undo"):
        "undoes an unfinished in-place addition; not a run of the tool",
}


def _scanned() -> list[Path]:
    """The Datasets tool controllers, and the converters' shared one."""
    return [p for p in python_sources(roots=("datasets",))
            if p.name.endswith("controller.py") or p.name == "packed_convert.py"]


def _worker_starts(path: Path):
    for func in ast.walk(parse_source(path)):
        if not isinstance(func, ast.FunctionDef):
            continue
        for node in ast.walk(func):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "start" and not node.args):
                target = node.func.value
                name = (target.id if isinstance(target, ast.Name)
                        else target.attr if isinstance(target, ast.Attribute) else "")
                if "worker" in name.lower():
                    yield func.name, node.lineno


def test_every_datasets_run_starts_through_start_worker():
    bad = []
    for path in _scanned():
        where = rel(path)
        for func, line in _worker_starts(path):
            if (where, func) not in NOT_A_RUN:
                bad.append(f"{where}:{line} in {func}")
    assert not bad, ("a run worker started without DatasetToolController._start_worker "
                     "never tells the queue it is running: " + ", ".join(bad))


def test_every_exemption_still_names_a_worker_start():
    for (where, func) in NOT_A_RUN:
        assert any(f == func for f, _ in _worker_starts(REPO / where)), f"stale exemption {where}::{func}"
