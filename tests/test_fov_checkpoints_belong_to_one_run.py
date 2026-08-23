"""One plate's per-FOV checkpoints must never be resumed by another.

Staging went to `scratch_base_for(output_dir) / f"_tmp_images_{well}"`, and
`scratch_base_for` returns one directory for the whole machine —
`$ALLWELL_SCRATCH_DIR`, or `~/.allwell/scratch`. The checkpoint inside was keyed
on the input filename stem. Nothing in either key named the dataset, the output
directory, the run or the settings, and `_load_fov_checkpoint` validated only a
schema-version string.

Acquisition software writes the same filename template for every plate, so the
stems collided **by default rather than by accident**. Measured: two different
output directories resolved to the same scratch path, and plate B loaded plate
A's per-cell records verbatim — `gfp_mean_intensity: 111.0` — then wrote them
into plate B's CSV under plate B's well labels.

Stop is SIGKILL (`pipeline/suspend.py`), so the `finally: remove_directory(...)`
never runs and a stopped plate leaves the directory in place for the next one.

`--force` did not help, and that is the second half: `process_well_zip_task`
took no `force` parameter at all, so ticking Force re-ran the well and still
resumed the old checkpoints. Change a threshold, tick Force, get byte-identical
numbers back.
"""
from __future__ import annotations

from pathlib import Path

from process_microscopy import (
    _load_fov_checkpoint,
    _save_fov_checkpoint,
    _staging_dir_for,
)
from util.settings_digest import settings_digest

RUN_A = settings_digest({"segmentation_method": "stardist_nuclei",
                         "stardist_prob_thresh": 0.2, "fluor_tokens": ["GFP"]})
RUN_B = settings_digest({"segmentation_method": "cellpose",
                         "min_nucleus_area_px": 200, "fluor_tokens": ["GFP"]})
FOV = "exp_GFP_A01_F001_T01"
RECORDS = [{"well": "A01", "nucleus_id": 1, "gfp_mean_intensity": 111.0}]


def test_two_plates_do_not_share_a_staging_directory(tmp_path, monkeypatch):
    """The reported shape: same well name, same filenames, different plates."""
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(tmp_path / "scratch"))
    a = tmp_path / "plateA" / "out"
    b = tmp_path / "plateB" / "out"
    a.mkdir(parents=True)
    b.mkdir(parents=True)

    dir_a = _staging_dir_for(a, "A01", RUN_A)
    dir_b = _staging_dir_for(b, "A01", RUN_A)

    assert dir_a != dir_b, (
        "two plates resolved to the same staging directory, so the second "
        f"resumes the first's checkpoints: {dir_a}")


def test_plate_b_cannot_load_plate_as_records(tmp_path, monkeypatch):
    """Asserted on the records, because that is what reaches the CSV."""
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(tmp_path / "scratch"))
    a = tmp_path / "plateA" / "out"
    b = tmp_path / "plateB" / "out"
    a.mkdir(parents=True)
    b.mkdir(parents=True)

    dir_a = _staging_dir_for(a, "A01", RUN_A)
    dir_a.mkdir(parents=True, exist_ok=True)
    _save_fov_checkpoint(dir_a, FOV, RECORDS, RUN_A)

    dir_b = _staging_dir_for(b, "A01", RUN_A)
    assert _load_fov_checkpoint(dir_b, FOV, RUN_A) is None, (
        "plate B resumed plate A's per-cell measurements and would have "
        "written them into its own CSV under its own well labels")


def test_changing_the_settings_moves_the_staging_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(tmp_path / "scratch"))
    out = tmp_path / "plate" / "out"
    out.mkdir(parents=True)

    assert _staging_dir_for(out, "A01", RUN_A) != _staging_dir_for(out, "A01", RUN_B), (
        "the same plate re-run with a different segmenter reused the "
        "intermediates the previous settings produced")


def test_a_checkpoint_states_the_run_it_belongs_to(tmp_path):
    """Belt as well as braces: the payload carries the key, not just the path.

    The directory name already isolates runs, so this should be unreachable —
    but this file's contents become CSV rows, and a stale one is not a slow run,
    it is another configuration's numbers published under this one's labels.
    """
    d = tmp_path / "staging"
    d.mkdir()
    _save_fov_checkpoint(d, FOV, RECORDS, RUN_A)

    assert _load_fov_checkpoint(d, FOV, RUN_A) == RECORDS
    assert _load_fov_checkpoint(d, FOV, RUN_B) is None, (
        "a checkpoint written under one configuration was handed to another")


def test_a_checkpoint_written_before_this_existed_is_still_accepted(tmp_path):
    """Grandfathering, matching the per-well settings marker."""
    import json

    d = tmp_path / "staging"
    d.mkdir()
    _save_fov_checkpoint(d, FOV, RECORDS, "")

    payload = json.loads((d / f".fov_{FOV}.records.json").read_text())
    assert payload.get("settings") == "", payload
    assert _load_fov_checkpoint(d, FOV, RUN_A) == RECORDS, (
        "a checkpoint from before this record existed was discarded, which "
        "would throw away a resumed run's work for no integrity gain")


def test_the_staging_name_still_carries_the_well(tmp_path, monkeypatch):
    """A human has to be able to find these directories when a run leaks one."""
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(tmp_path / "scratch"))
    out = tmp_path / "plate" / "out"
    out.mkdir(parents=True)

    name = Path(_staging_dir_for(out, "B07", RUN_A)).name
    assert name.startswith("_tmp_images_"), name
    assert name.endswith("_B07"), (
        f"the sweep and every debugging recipe look for the well here: {name}")
