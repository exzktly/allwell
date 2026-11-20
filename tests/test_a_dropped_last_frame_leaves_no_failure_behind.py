"""A plate whose damaged last frame is left out reads as whole, however it was left out.

The report: the last frame of a CZI is sometimes corrupt or missing, in one channel or all of them. Analysed, the
well fails that timepoint. The user then leaves it out — by converting again with a timepoint subset and analysing
into the same output folder with *Fill in what's missing*, or by cutting the analysed dataset with Datasets ▸
Subset — and both times the dataset went on warning that the run failed, and the Nanowells QC page said the
identity check had not been run.

Both were the dropped timepoint's records outliving it:

* the incremental run found every timepoint the input still holds already finished and returned the well as up to
  date, so its grid record, run report and identity verdicts still carried the dropped timepoint's failure
  (``pipeline.nanowell_task._prior_state`` now declines a prior that holds a timepoint the input no longer does);
* Subset copied the source run's ``run_report`` and ``run_check`` unchanged, naming the dropped timepoint as
  missing, and did not carry the identity check's sidecar — which, carried, would have been stale anyway, since it
  is a digest of tables the subset rewrites (Subset now carries neither block and runs the check on what it wrote).

Driven through ``process_microscopy.main`` on rendered tiles and the real Subset ``execute_plan``, with a well whose
last timepoint has its phase images and no fluorescence — the shape a CZI conversion leaves when one channel's last
plane cannot be read (``pipeline.czi_explode`` skips it and writes the rest).
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import zipfile

import numpy as np
import pytest
from tifffile import imwrite

import pipeline.suspend as _suspend
import process_microscopy as pm
from pipeline import nanowell_measure as nm
from pipeline import well_skip
from pipeline.nanowell_identity import CONFIRMED, NOT_CHECKED, REFERENCE, verdicts
from tests._nanowell_run_fixture import build_well
from util.acquisition_record import write_record_file

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

WELL = "A01"
SCHEMA = ("experiment", "channel", "well", "fov", "timepoint")
ALL_TIMEPOINTS = ("0h", "24h", "48h")
KEPT = ("0h", "24h")


def _one_cell_per_nanowell():
    """A segmenter that finds one small cell in the middle of every nanowell crop, so every measured timepoint has
    cell rows (a subset refuses a well whose kept rows are none) and the result does not depend on the render."""
    def segment(rim):
        h, w = np.asarray(rim).shape
        yy, xx = np.ogrid[:h, :w]
        return ((yy - h // 2) ** 2 + (xx - w // 2) ** 2 <= 9).astype(int)
    return segment


def _write_input(plate, rendered, record0, timepoints, *, no_fluor_at=None):
    """``plate/in``: *timepoints* of the rendered well, with no fluorescence image at *no_fluor_at*."""
    shutil.rmtree(plate / "in", ignore_errors=True)
    (plate / "in").mkdir(parents=True)
    record = copy.deepcopy(record0)
    record["timepoints"] = {tp: copy.deepcopy(record0["timepoints"]["0h"]) for tp in timepoints}
    record["tiles"] = [{**tile, "timepoint": tp} for tp in timepoints for tile in record0["tiles"]]
    tif = plate / "frame.tif"
    with zipfile.ZipFile(plate / "in" / f"{WELL}.zip", "w") as zf:
        for tp in timepoints:
            for fov, image in rendered.images.items():
                phase = np.array(image, dtype="float32")
                for channel, pixels in (("PHASE", phase), ("GFP", phase * 0 + 10.0)):
                    if channel == "GFP" and tp == no_fluor_at:
                        continue
                    imwrite(str(tif), pixels)
                    zf.write(tif, f"exp_{channel}_{WELL}_{fov}_{tp}.tif")
    tif.unlink()
    write_record_file(plate / "in" / "acquisition.json", record)


def _run(monkeypatch, plate, *extra):
    def _serial(*, well_zips, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix,
                schema, sep, incremental, incremental_prior_dir, settings_key, force, force_wells=None, **_ignored):
        forced = well_skip._canonical_force_wells(force_wells)
        for label, zip_path in well_zips:
            pm.process_well_zip_task(label, zip_path, output_dir, shared_kwargs_template, nuclear_token,
                                     fluor_tokens, csv_prefix, schema, sep,
                                     well_skip.incremental_for_well(incremental, label, forced),
                                     incremental_prior_dir, settings_key, force or label in forced)
        return []

    monkeypatch.setattr(pm, "process_well_zips", _serial)
    monkeypatch.setattr(nm, "cellpose_segmenter", lambda *a, **k: _one_cell_per_nanowell())
    monkeypatch.setattr(_suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(_suspend, "watch_parent", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", [
        "process_microscopy.py", "--input_dir", str(plate / "in"), "--output_dir", str(plate / "out"),
        "--phase_token", "PHASE", "--fluor_tokens", "GFP", "--filename_schema", ":".join(SCHEMA),
        "--workers", "1", "--background", "none", "--flatfield", "none", "--no_auto_threshold",
        "--nanowell_format", "100um", *extra])
    try:
        pm.main()
    except SystemExit:
        pass                     # a run with a failed timepoint exits non-zero; what it wrote is the subject


def _info(out) -> dict:
    return json.loads((out / "pipeline_info.json").read_text())


def _grid_timepoints(out) -> dict:
    grid = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())
    return {tp: block.get("status") for tp, block in grid["timepoints"].items()}


@pytest.fixture(scope="module")
def rendered():
    return build_well(timepoint="0h")


@pytest.fixture(scope="module")
def failed_plate(tmp_path_factory, rendered):
    """A plate analysed with its last timepoint's fluorescence missing: the run the user saw fail."""
    plate = tmp_path_factory.mktemp("dropped_last_frame") / "plate"
    _write_input(plate, *rendered, ALL_TIMEPOINTS, no_fluor_at="48h")
    with pytest.MonkeyPatch.context() as mp:
        _run(mp, plate)
    out = plate / "out"
    # What the report describes, before either way of dropping the frame: the run recorded the timepoint as failed.
    assert _grid_timepoints(out)["48h"] != "ok"
    assert _info(out)["run_report"]["incomplete_wells"] == [WELL]
    return plate


def _copy(plate, tmp_path):
    dest = tmp_path / "plate"
    shutil.copytree(plate, dest)
    return dest


def test_filling_in_after_converting_without_the_frame_rewrites_the_well(failed_plate, rendered, tmp_path,
                                                                          monkeypatch):
    plate = _copy(failed_plate, tmp_path)
    _write_input(plate, *rendered, KEPT)
    _run(monkeypatch, plate, "--incremental")
    out = plate / "out"

    assert _grid_timepoints(out) == {"0h": "ok", "24h": "ok"}, (
        "the well was kept as up to date, so its output still carries the timepoint its input no longer has")
    report = _info(out)["run_report"]
    assert report["incomplete_wells"] == [], f"the dropped timepoint is still reported missing: {report}"
    assert report["wells"][WELL]["groups_total"] == len(KEPT)
    assert _info(out)["run_check"]["wells"][WELL]["health"] == "complete"
    assert verdicts(out, WELL, KEPT) == {"0h": (REFERENCE, "the reference timepoint"),
                                         "24h": (CONFIRMED, verdicts(out, WELL, KEPT)["24h"][1])}


def test_a_subset_without_the_frame_reads_as_whole_and_checked(failed_plate, tmp_path):
    from datasets.subset import apply as subset_apply
    from datasets.subset import subset_compute as sc
    from datasets.validate.validate_compute import build_report, well_health

    dest = tmp_path / "subset"
    said: list[str] = []
    plan = sc.build_subset_plan(failed_plate, dest, sc.SubsetFilter(timepoint_labels=frozenset(KEPT)))
    subset_apply.execute_plan(plan, log=said.append)
    out = dest / "out"

    info = _info(out)
    assert "run_report" not in info and "run_check" not in info, (
        "the source run's record of the dropped timepoint's failure was carried into the subset")
    found = verdicts(out, WELL, KEPT)
    assert found["0h"][0] == REFERENCE and found["24h"][0] == CONFIRMED, (
        f"QC reads the subset's identity check as {found}; it must have been run on the tables the subset wrote")
    assert NOT_CHECKED not in {verdict for verdict, _why in found.values()}
    assert any(line.startswith("Ran the nanowell identity check on 1 well(s)") for line in said), said
    report = build_report(dest)
    assert {w.well: well_health(w) for w in report.wells} == {WELL: "complete"}
