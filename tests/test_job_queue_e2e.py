"""End-to-end test for the job queue: real job chains driven through
QueueBridge with the real per-kind controllers — real QThread workers, real
files on disk, no fakes.

Offscreen Qt (the controllers are QObjects with real page widgets), unlike
the other Datasets features' Qt-free compute-layer E2E tests — this is
specifically testing the queue's own scheduling/chaining/attribution against
real adapters, which only exist at the Qt layer.

Two chain shapes are covered:

* A two-job chain (Subset -> Validate).
* A longer, three-kind chain (Concatenate -> Validate -> Subset) exercising
  a chain_target/chain_kind pairing the two-job chain never touches
  (Concatenate's "dataset_root" output feeding Validate's "dataset" field),
  plus the cascade-on-failure path two levels deep.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from datasets.concatenate.controller import ConcatenateController
from datasets.job_queue.controller import QueueBridge
from datasets.subset.controller import SubsetController
from datasets.validate.controller import ValidateController
from services.job_queue import JobSpec, JobState
from tests.fixtures.dataset_builder import build_dataset
from well_viewer.data.data_loading import load_well_csv
from well_viewer.domain.viewer_state import read_pipeline_info


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


def test_concatenate_validate_subset_three_kind_chain_runs_against_real_output(qapp, tmp_path):
    """Three distinct kinds chained end to end: Concatenate -> Validate ->
    Subset — a longer chain than the module's other two tests' single
    Subset -> Validate pair, and the first coverage of Concatenate as a
    chain link. Each stage's chain_target/chain_kind pairing is exercised
    for real: Concatenate produces "dataset_root" (QueueBridge._PRODUCED_KEY),
    Validate's successor reads it via chain_target "dataset"
    (ValidateController.show_job), and Validate itself produces
    "dataset_root" that Subset's successor reads via chain_target "source"
    (SubsetController.show_job). The final dataset is reopened through
    Review's own loaders to confirm it's genuinely readable, not just
    "a worker reported success".
    """
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    concat_dest = tmp_path / "concat_dest"
    final_dest = tmp_path / "final_dest"
    # Disjoint wells on each side exercises concatenate_compute's
    # "copies unique wells and shifts ds2-only" path: ds1's last timepoint
    # (24h) plus its own median interval (24h) pushes every ds2 timepoint
    # (0h/24h) to continue the plate at 48h/72h.
    build_dataset(ds1, wells=["A01", "B02"], timepoints=["0h", "24h"])
    build_dataset(ds2, wells=["C03", "D04"], timepoints=["0h", "24h"])

    concat_ctrl = ConcatenateController(on_open_in_review=lambda _p: None)
    validate_ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    subset_ctrl = SubsetController(on_open_in_review=lambda _p: None)
    bridge = QueueBridge(state_dir=tmp_path / "queue-state-3")
    bridge.register("concatenate", concat_ctrl)
    bridge.register("validate", validate_ctrl)
    bridge.register("subset", subset_ctrl)

    bridge.add_job(JobSpec(
        id="c1", kind="concatenate", label="Concatenate",
        spec={"dataset1": str(ds1), "dataset2": str(ds2), "destination": str(concat_dest)},
    ))
    bridge.add_job(JobSpec(
        id="v1", kind="validate", label="Validate",
        spec={"dataset": "", "apply_repair": False},
        chain_from="c1", chain_target="dataset", chain_kind="dataset_root",
    ))
    bridge.add_job(JobSpec(
        id="s1", kind="subset", label="Subset",
        spec={
            "source": "", "destination": str(final_dest),
            # One well from each origin dataset -- confirms the filter works
            # against the merged dataset, not either original source.
            "wells": ["A01", "C03"], "timepoint_labels": None, "fovs": None,
        },
        chain_from="v1", chain_target="source", chain_kind="dataset_root",
    ))

    bridge.start()
    assert bridge.queue.get("c1").state == JobState.RUNNING
    assert bridge.queue.get("v1").state == JobState.PENDING  # gated on c1
    assert bridge.queue.get("s1").state == JobState.PENDING  # gated on v1

    _run_worker_to_completion(concat_ctrl)

    assert bridge.queue.get("c1").state == JobState.SUCCEEDED
    assert bridge.queue.get("c1").dataset_root == str(concat_dest)
    # Concatenate's real output on disk: ds1's wells keep their own
    # timepoints, ds2's wells were relabelled to continue after ds1.
    with (concat_dest / "out" / "measurements_A01.csv").open() as fh:
        assert "24h" in fh.read()
    with (concat_dest / "out" / "measurements_C03.csv").open() as fh:
        c03_text = fh.read()
    assert "48h" in c03_text and "72h" in c03_text

    # v1 must have been materialised against the folder Concatenate actually
    # produced, not a placeholder -- the whole point of chaining. It may
    # already be underway (small dataset, same event-loop drain as
    # concat_ctrl's own completion), so assert it's at least past PENDING.
    assert bridge.queue.get("v1").state != JobState.PENDING
    assert validate_ctrl._worker is not None

    _run_worker_to_completion(validate_ctrl)

    assert bridge.queue.get("v1").state == JobState.SUCCEEDED
    assert "Clean" in bridge.queue.get("v1").message
    # Validate actually ran against concat_dest, not either original source.
    assert validate_ctrl.page.dataset_path() == str(concat_dest)

    assert bridge.queue.get("s1").state != JobState.PENDING
    assert subset_ctrl._worker is not None

    _run_worker_to_completion(subset_ctrl)

    assert bridge.queue.get("s1").state == JobState.SUCCEEDED
    assert bridge.queue.get("s1").dataset_root == str(final_dest)
    assert subset_ctrl.page.source_path() == str(concat_dest)

    # The final dataset genuinely only holds the two chosen wells (one from
    # each origin dataset) -- not a stale copy of either source.
    csv_wells = sorted(
        p.stem.rsplit("_", 1)[-1] for p in (final_dest / "out").glob("measurements_*.csv")
    )
    assert csv_wells == ["A01", "C03"]
    assert not (final_dest / "out" / "measurements_B02.csv").exists()
    assert not (final_dest / "out" / "measurements_D04.csv").exists()

    # Genuinely readable by Review's own loaders, not just "a worker
    # reported success" -- the point of chaining through to a real reopen.
    _extract_fn, fluor_tokens, _smfish_tokens, pipeline_info = read_pipeline_info(
        final_dest / "out"
    )
    assert pipeline_info["schema"] == "experiment:channel:well:fov:timepoint"
    assert fluor_tokens == ["nir", "gfp"]

    df_a01 = load_well_csv(final_dest / "out" / "measurements_A01.csv")
    assert sorted(df_a01["timepoint"].unique()) == ["0h", "24h"]
    df_c03 = load_well_csv(final_dest / "out" / "measurements_C03.csv")
    assert sorted(df_c03["timepoint"].unique()) == ["48h", "72h"]

    concat_ctrl.shutdown()
    validate_ctrl.shutdown()
    subset_ctrl.shutdown()


def test_failed_concatenate_blocks_validate_and_subset_two_levels_deep(qapp, tmp_path):
    """Cascade-on-failure across a THREE-stage chain, not just the two-stage
    case ``test_failed_predecessor_blocks_successor_which_never_runs``
    already covers: the FIRST job in a longer chain fails for a real reason
    (Concatenate refuses a dataset 1 with only one timepoint — there's no
    imaging cadence to continue from), and both of its transitive
    successors — one directly chained, one chained two levels down — land
    in ``blocked``, never ``failed`` and never ``pending``, and neither of
    their controllers' workers ever start.
    """
    ds1 = tmp_path / "ds1_bad"
    ds2 = tmp_path / "ds2"
    concat_dest = tmp_path / "concat_dest"
    final_dest = tmp_path / "final_dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])  # single timepoint -> no cadence
    build_dataset(ds2, wells=["C03"], timepoints=["0h", "24h"])

    concat_ctrl = ConcatenateController(on_open_in_review=lambda _p: None)
    validate_ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    subset_ctrl = SubsetController(on_open_in_review=lambda _p: None)
    bridge = QueueBridge(state_dir=tmp_path / "queue-state-4")
    bridge.register("concatenate", concat_ctrl)
    bridge.register("validate", validate_ctrl)
    bridge.register("subset", subset_ctrl)

    bridge.add_job(JobSpec(
        id="c1", kind="concatenate", label="Concatenate",
        spec={"dataset1": str(ds1), "dataset2": str(ds2), "destination": str(concat_dest)},
    ))
    bridge.add_job(JobSpec(
        id="v1", kind="validate", label="Validate",
        spec={"dataset": "", "apply_repair": False},
        chain_from="c1", chain_target="dataset", chain_kind="dataset_root",
    ))
    bridge.add_job(JobSpec(
        id="s1", kind="subset", label="Subset",
        spec={
            "source": "", "destination": str(final_dest),
            "wells": None, "timepoint_labels": None, "fovs": None,
        },
        chain_from="v1", chain_target="source", chain_kind="dataset_root",
    ))

    bridge.start()
    _run_worker_to_completion(concat_ctrl)

    assert bridge.queue.get("c1").state == JobState.FAILED
    assert "one timepoint" in bridge.queue.get("c1").message
    assert bridge.queue.get("v1").state == JobState.BLOCKED
    assert bridge.queue.get("s1").state == JobState.BLOCKED
    assert "c1" in bridge.queue.get("v1").message
    assert "predecessor" in bridge.queue.get("s1").message
    assert validate_ctrl._worker is None  # v1 never started
    assert subset_ctrl._worker is None    # s1 -- two levels down -- never started either
    # Neither downstream controller's page was ever repopulated from the
    # (nonexistent) predecessor output.
    assert validate_ctrl.page.dataset_path() == ""
    assert subset_ctrl.page.source_path() == ""

    concat_ctrl.shutdown()
    validate_ctrl.shutdown()
    subset_ctrl.shutdown()
