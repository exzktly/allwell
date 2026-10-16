"""The end-of-run pass: a (well, timepoint) with no evidence of its own is anchored from the rest of the plate (P3)."""

from __future__ import annotations

import csv
import json
import logging
import sys
import zipfile

import numpy as np
import pytest
from tifffile import imwrite

import pipeline.nanowell_lattice as lattice
import pipeline.suspend as _suspend
import process_microscopy as pm
from pipeline import nanowell_measure as nm
from pipeline import nanowell_run as nr
from pipeline import nanowell_task as nt
from tests._nanowell_run_fixture import PX, build_well, threshold_segmenter
from tests._nanowell_synth import _screen_rotation
from util.acquisition_record import write_record_file

WELLS = {"A01": (0.0, 0.0), "A02": (9000.0, 0.0), "B01": (0.0, 9000.0), "B02": (9000.0, 9000.0)}
CENTRE = np.array([4500.0, 4500.0])
TILE = (700, 700)
UNION = np.array(TILE) * PX / 2
THETA = 3.0                                              # degrees the plate turns between timepoints
SHIFT = np.array([150.0, -100.0])


def _grid(origin_stage, status="ok", method="fiducial", offset=(10.0, 20.0), montage_origin=(0.0, 0.0)):
    return {"status": status, "origin_method": method, "origin_stage_um": origin_stage,
            "stage_offset_um": list(offset) if offset is not None else None, "montage_origin_um": list(montage_origin)}


def _plate_grids(target_status="unaddressed", target_ref_method="fiducial", with_stage=True):
    """Four wells at two timepoints, the plate moved rigidly in between; the target well B02 lost its origin."""
    rotation = _screen_rotation(THETA)
    grids = {}
    for well, q in WELLS.items():
        s0 = np.array(q) + UNION
        s1 = rotation @ (s0 - CENTRE) + CENTRE + SHIFT
        target = well == "B02"
        grids[well] = {"scheme_version": nt.SCHEME_VERSION, "well": well, "timepoints": {
            "0h": _grid(list(s0), method=target_ref_method if target else "fiducial"),
            "24h": _grid(None if target else list(s1), status=target_status if target else "ok",
                         method=None if target else "fiducial", offset=(10.0, 20.0) if with_stage else None,
                         montage_origin=(5.0, 7.0))}}
    return grids, rotation @ (np.array(WELLS["B02"]) + UNION - CENTRE) + CENTRE + SHIFT


def test_the_other_wells_predict_where_a_wells_origin_is_now():
    grids, truth = _plate_grids()
    predictions, reasons = nt.plan_reanchor(grids)
    prediction = predictions["B02"]["24h"]
    assert reasons == {}
    # In the target's own montage frame: stage position, less the offset of the common frame, less the montage's origin.
    expected = truth - np.array([10.0, 20.0]) - np.array([5.0, 7.0])
    assert np.allclose(prediction.position, expected, atol=0.05)
    assert prediction.uncertainty_with_site is not None and prediction.uncertainty_with_site < 1.0


@pytest.mark.parametrize("change, reason", [
    (dict(with_stage=False), "stage position"),
    (dict(target_ref_method="plate_rigid"), "never anchored on its own"),
])
def test_what_cannot_be_predicted_says_why(change, reason):
    grids, _ = _plate_grids(**change)
    predictions, reasons = nt.plan_reanchor(grids)
    assert predictions == {} and reason in reasons[("B02", "24h")]


def test_fewer_than_three_other_wells_is_not_enough():
    grids, _ = _plate_grids()
    del grids["A02"], grids["B01"]
    predictions, reasons = nt.plan_reanchor(grids)
    assert predictions == {} and "plate's motion could not be fitted" in reasons[("B02", "24h")]


def test_an_anchor_that_itself_came_from_the_plate_is_not_evidence_for_another_well():
    grids, _ = _plate_grids()
    for well in ("A01", "A02", "B01"):
        grids[well]["timepoints"]["24h"]["origin_method"] = "plate_rigid"
    predictions, reasons = nt.plan_reanchor(grids)
    assert predictions == {} and ("B02", "24h") in reasons


# ── end to end ───────────────────────────────────────────────────────────────
def _write_plate(tmp_path, dark_rim=()):
    """Four 1-tile wells imaged twice; between the timepoints the plate turns by THETA and shifts. *dark_rim* are
    sites of B02 drawn dark at 0h (a bubble along its rim)."""
    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    rotation = _screen_rotation(THETA)
    tif = tmp_path / "frame.tif"
    record = None
    truth = {}
    for well, q in WELLS.items():
        s0 = np.array(q) + UNION
        s1 = rotation @ (s0 - CENTRE) + CENTRE + SHIFT
        camera1 = np.array(q) + (20.0, -10.0)             # the camera revisits each well roughly where it was
        first, rec0 = build_well(rows=1, cols=1, tile=TILE, well=well, timepoint="0h", seed=11, radius_fraction=0.26,
                                 dark_sites=dark_rim if well == "B02" else ())
        second, rec1 = build_well(rows=1, cols=1, tile=TILE, well=well, timepoint="24h", seed=12,
                                  rotation=7.0 + THETA, array_shift_um=tuple(s1 - camera1 - UNION), radius_fraction=0.26)
        for rec, camera in ((rec0, np.array(q)), (rec1, camera1)):
            for tile in rec["tiles"]:
                tile["stage_xy_um"] = list(camera + np.array(tile["mosaic_xy_px"]) * PX)
        rec0["timepoints"].update(rec1["timepoints"])
        rec0["tiles"] += rec1["tiles"]
        if record is None:
            record = rec0
        else:
            record["timepoints"].update(rec0["timepoints"])
            record["tiles"] += rec0["tiles"]
        truth[well] = second
        with zipfile.ZipFile(plate / "in" / f"{well}.zip", "w") as zf:
            for token, rendered in (("0h", first), ("24h", second)):
                for fov, image in rendered.images.items():
                    for channel, frame in (("PHASE", image), ("GFP", image * 0 + 10.0)):
                        imwrite(str(tif), frame.astype("float32"))
                        zf.write(tif, f"exp_{channel}_{well}_{fov}_{token}.tif")
    write_record_file(plate / "in" / "acquisition.json", record)
    return plate, truth


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


#: Every log line of the fixture's run, for what Analyze would read from it.
_RUN_LOG = _ListHandler()


@pytest.fixture(scope="module")
def reanchored(tmp_path_factory):
    return _run_reanchoring(tmp_path_factory.mktemp("reanchor"))


def _run_reanchoring(tmp_path, dark_rim=()):
    plate, truth = _write_plate(tmp_path, dark_rim)
    real_locate, real_resolve = nr.locate_timepoint, lattice.resolve_origin
    state = {"target": False}

    def locate(tp, record, well, *args, **kwargs):
        state["target"] = (well, tp) == ("B02", "24h")
        return real_locate(tp, record, well, *args, **kwargs)

    def resolve(fit, *, fiducial=None, array_centre=None, plate_rigid=None, fiducial_margin):
        if state["target"]:
            fiducial = array_centre = None                   # a well whose square and boundary give nothing
        return real_resolve(fit, fiducial=fiducial, array_centre=array_centre, plate_rigid=plate_rigid,
                            fiducial_margin=fiducial_margin)

    def serial(*, well_zips, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix, schema, sep,
               incremental, incremental_prior_dir, settings_key, force, force_wells=None, **_ignored):
        for label, zip_path in well_zips:
            pm.process_well_zip_task(label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens,
                                     csv_prefix, schema, sep, incremental, incremental_prior_dir, settings_key, force)
        return []

    patches = [(pm, "process_well_zips", serial), (nr, "locate_timepoint", locate),
               (lattice, "resolve_origin", resolve), (nm, "cellpose_segmenter", lambda *a, **k: threshold_segmenter()),
               (_suspend, "install_handlers", lambda **k: None), (_suspend, "watch_parent", lambda *a, **k: None)]
    originals = [(obj, name, getattr(obj, name)) for obj, name, _ in patches]
    for obj, name, value in patches:
        setattr(obj, name, value)
    argv = sys.argv
    task_log = logging.getLogger("allwell.nanowell")            # pipeline/nanowell_task.py's logger
    task_log.addHandler(_RUN_LOG)
    level = task_log.level
    task_log.setLevel(logging.INFO)
    try:
        sys.argv = ["process_microscopy.py", "--input_dir", str(plate / "in"), "--output_dir", str(plate / "out"),
                    "--phase_token", "PHASE", "--fluor_tokens", "GFP", "--filename_schema",
                    "experiment:channel:well:fov:timepoint", "--workers", "1", "--background", "none",
                    "--flatfield", "none", "--no_auto_threshold", "--no_run_check", "--nanowell_format", "100um"]
        pm.main()
    finally:
        task_log.removeHandler(_RUN_LOG)
        task_log.setLevel(level)
        sys.argv = argv
        for obj, name, value in originals:
            setattr(obj, name, value)
    return plate / "out", truth


def test_the_well_with_no_evidence_of_its_own_is_addressed_from_the_plate_and_correctly(reanchored):
    out, truth = reanchored
    grid = json.loads((out / "nanowells" / "B02_grid.json").read_text())
    block = grid["timepoints"]["24h"]
    assert block["status"] == "ok" and block["origin_method"] == "plate_rigid"
    rendered = truth["B02"]
    drawn = {tuple(map(float, s)): a for s, a in zip(rendered.sites_um, rendered.array.addresses)}
    sites = np.array(list(drawn))
    rows = [r for r in csv.DictReader((out / "nanowells" / "B02.csv").open()) if r["timepoint"] == "24h"]
    assert len(rows) > 40
    wrong = 0
    for r in rows:
        if r["detected"] != "True" or not r["nw_q"]:
            continue
        nearest = tuple(sites[np.argmin(np.hypot(sites[:, 0] - float(r["well_x_um"]),
                                                 sites[:, 1] - float(r["well_y_um"])))])
        wrong += (int(r["nw_q"]), int(r["nw_r"])) != drawn[nearest]
    assert wrong == 0
    assert all(r["nw_q"] != "" for r in rows)                      # the timepoint is addressed, not left without


def test_the_summary_says_how_many_were_anchored_and_how_many_remain(reanchored):
    out, _ = reanchored
    info = json.loads((out / "pipeline_info.json").read_text())
    wells = sorted(p.name[: -len("_grid.json")] for p in (out / "nanowells").glob("*_grid.json"))
    assert len(wells) > 1
    # ... and which wells the plate fit could see: a batched remote run fits each batch alone (roll-up review X19).
    assert info["nanowell"]["reanchor"] == {"anchored": ["B02/24h"], "remaining": {}, "wells_considered": wells}
    other = json.loads((out / "nanowells" / "A01_grid.json").read_text())
    assert other["timepoints"]["24h"]["origin_method"] == "fiducial"        # untouched wells keep their own anchor


def test_a_re_anchored_well_finishes_once_in_the_progress_count(reanchored):
    """Roll-up review X31: the re-anchoring re-run logged "— done." a second time, which Analyze's progress reads
    as a repeated completion and warns about on every run that re-anchors. It says "— re-anchored." instead."""
    from services.pipeline_runner import ProgressTracker

    finished = [line.split(".")[0] for line in _RUN_LOG.lines
                if line.startswith(("Well B02 — done.", "Well B02 — re-anchored."))]
    assert finished == ["Well B02 — done", "Well B02 — re-anchored"]
    tracker = ProgressTracker()
    events = [event for line in _RUN_LOG.lines for event in tracker.parse(line)]
    assert [e for e in events if e[0] == "well_repeat"] == [] and tracker.well_repeats == 0


def test_a_re_anchored_timepoint_that_sees_a_rim_the_first_pass_did_not_widens_the_roster_for_every_timepoint(
        tmp_path_factory):
    """Tracker X35a-R1 on the re-anchoring path: B02's rim is dark at 0h, and its 24h, which sees it, is only addressed
    by the end-of-run pass. That pass re-runs the well incrementally against the roster the first pass kept from 0h
    alone; the 24h evidence changes it, so the well is analysed again over both timepoints."""
    probe, _ = build_well(rows=1, cols=1, tile=TILE, well="B02", timepoint="0h", seed=11, radius_fraction=0.26)
    basis = np.column_stack((probe.array.a1, probe.array.a2))
    rim = sorted(probe.array.roster, key=lambda a: -np.linalg.norm(basis @ np.asarray(a, float)))[:4]
    out, truth = _run_reanchoring(tmp_path_factory.mktemp("reanchor_rim"), rim)
    grid = json.loads((out / "nanowells" / "B02_grid.json").read_text())
    assert grid["timepoints"]["24h"]["origin_method"] == "plate_rigid"
    assert "reanalysed_because" in grid["array_radius"]
    roster = {tuple(a) for a in grid["array_radius"]["roster"]}
    assert set(rim) <= roster and len(roster) == len(probe.array.roster)
    rows = list(csv.DictReader((out / "nanowells" / "B02.csv").open()))
    assert {tp: sum(r["timepoint"] == tp for r in rows) for tp in ("0h", "24h")} == {"0h": len(roster),
                                                                                   "24h": len(roster)}
