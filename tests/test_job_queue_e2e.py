"""End-to-end test for the job queue: a real two-job chain (Subset ->
Validate) driven through QueueBridge with the real SubsetController and
ValidateController — real QThread workers, real files on disk, no fakes.

Offscreen Qt (the controllers are QObjects with real page widgets), unlike
the other Datasets features' Qt-free compute-layer E2E tests — this is
specifically testing the queue's own scheduling/chaining/attribution against
real adapters, which only exist at the Qt layer.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from datasets.job_queue.controller import QueueBridge
from datasets.subset.controller import SubsetController
from datasets.validate.controller import ValidateController
from services.job_queue import JobSpec, JobState
from tests.fixtures.dataset_builder import build_dataset


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _run_worker_to_completion(ctrl) -> None:
    """See tests/test_validate_page.py for why isRunning() polling is unsafe."""
    worker = ctrl._worker
    assert worker is not None
    worker.wait(5000)
    for _ in range(10):
        QApplication.processEvents()


def test_subset_then_validate_chain_runs_against_subsets_real_output(qapp, tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    build_dataset(src, wells=["A01", "B02"], timepoints=["0h", "24h"], fovs=("F001",))

    subset_ctrl = SubsetController(on_open_in_review=lambda _p: None)
    validate_ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    bridge = QueueBridge(state_dir=tmp_path / "queue-state")
    bridge.register("subset", subset_ctrl)
    bridge.register("validate", validate_ctrl)

    bridge.add_job(JobSpec(
        id="s1", kind="subset", label="Subset",
        spec={
            "source": str(src), "destination": str(dest),
            "wells": ["A01"], "timepoint_labels": None, "fovs": None,
        },
    ))
    bridge.add_job(JobSpec(
        id="v1", kind="validate", label="Validate", spec={"dataset": "", "apply_repair": False},
        chain_from="s1", chain_target="dataset", chain_kind="dataset_root",
    ))

    bridge.start()
    assert bridge.queue.get("s1").state == JobState.RUNNING
    assert bridge.queue.get("v1").state == JobState.PENDING  # gated on s1

    _run_worker_to_completion(subset_ctrl)

    assert bridge.queue.get("s1").state == JobState.SUCCEEDED
    assert bridge.queue.get("s1").dataset_root == str(dest)
    # v1 must have been materialised against the folder Subset actually
    # produced, not a placeholder — the whole point of chaining. Its own
    # worker may already have finished too (small dataset, same event-loop
    # drain), so assert it's at least underway rather than still PENDING.
    assert bridge.queue.get("v1").state != JobState.PENDING
    assert validate_ctrl._worker is not None

    _run_worker_to_completion(validate_ctrl)

    assert bridge.queue.get("v1").state == JobState.SUCCEEDED
    assert "Clean" in bridge.queue.get("v1").message
    # Validate actually ran against dest, not src - confirmed by the report
    # having been built (and shown) from that folder.
    assert validate_ctrl.page.dataset_path() == str(dest)

    subset_ctrl.shutdown()
    validate_ctrl.shutdown()


def test_failed_predecessor_blocks_successor_which_never_runs(qapp, tmp_path):
    src = tmp_path / "src"
    build_dataset(src, wells=["A01"], timepoints=["0h"])
    # An invalid destination (same as source) makes Subset's own planner
    # refuse the run - a real failure, not a simulated one.
    bad_dest = src

    subset_ctrl = SubsetController(on_open_in_review=lambda _p: None)
    validate_ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    bridge = QueueBridge(state_dir=tmp_path / "queue-state-2")
    bridge.register("subset", subset_ctrl)
    bridge.register("validate", validate_ctrl)

    bridge.add_job(JobSpec(
        id="s1", kind="subset", label="Subset",
        spec={
            "source": str(src), "destination": str(bad_dest),
            "wells": ["A01"], "timepoint_labels": None, "fovs": None,
        },
    ))
    bridge.add_job(JobSpec(
        id="v1", kind="validate", label="Validate", spec={"dataset": "", "apply_repair": False},
        chain_from="s1", chain_target="dataset", chain_kind="dataset_root",
    ))

    bridge.start()
    _run_worker_to_completion(subset_ctrl)

    assert bridge.queue.get("s1").state == JobState.FAILED
    assert bridge.queue.get("v1").state == JobState.BLOCKED
    assert validate_ctrl._worker is None  # v1 never started

    subset_ctrl.shutdown()
    validate_ctrl.shutdown()
