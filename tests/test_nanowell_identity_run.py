"""The identity check over the real pipeline's output (NANOWELL_IDENTITY_TRACKER E9, E10).

``test_nanowell_identity.py`` injects each fault into tables built from the renderer's ground truth. This file
runs the real detection and addressing on rendered tiles and checks what the run actually wrote:

* a well whose second timepoint could not read its square and was anchored on its fitted array centre a step off
  is put right by the run (``nanowell_run._settle_steps``), and every address lands on itself — and with that repair
  disabled, the check is what catches the step, naming it (E10, the mutation that proves the check bites on
  the pipeline's own output rather than on hand-built tables);
* ``process_microscopy.main`` writes ``nanowells/<well>_identity.json`` for a two-timepoint well, confirmed, and
  Review-side :func:`verdicts` reads it back (E9).
"""
from __future__ import annotations

import json
import sys
import zipfile
import threading
from dataclasses import replace

import numpy as np
import pytest
from tifffile import imwrite

from pipeline import nanowell_detect as nd
from pipeline import nanowell_identity as ni
from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import build_well, threshold_segmenter

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

SETTINGS = nr.NanowellSettings("100um")
#: Each timepoint's (rotation, array shift, seed): the plate turned and moved between them, the cells redrawn.
POSES = {"T0": (7.0, (0.0, 0.0), 1), "T1": (8.5, (50.0, -35.0), 2)}


def _well(cells=40):
    record, images = None, {}
    for tp, (rotation, shift, seed) in POSES.items():
        rendered, rec = build_well(timepoint=tp, seed=seed, rotation=rotation, array_shift_um=shift, cells=cells)
        images[tp] = rendered.images
        if record is None:
            record = rec
        else:
            record["timepoints"].update(rec["timepoints"])
            record["tiles"] += rec["tiles"]
    return record, images


@pytest.fixture(scope="module")
def well():
    return _well()


def _t1_misses_its_square_and_its_centre_is_a_step_off(monkeypatch):
    """At T1 the run does not accept the square (as under a bubble or a poor focus) and its fitted array centre is
    one ``a1`` step off, so T1 is anchored on the array centre a site away from where T0 put its origin."""
    real_detect, real_locate, real_find = nd.detect_sites, nr.locate_timepoint, nr._find_square
    current = threading.local()           # per thread: run_well locates timepoints on several at once

    def detect(*args, **kwargs):
        fit = real_detect(*args, **kwargs)
        if getattr(current, "tp", None) == "T1" and fit.ok and fit.boundary_centre_um is not None:
            fit = replace(fit, boundary_centre_um=tuple(np.asarray(fit.boundary_centre_um) + np.asarray(fit.fit.a1)))
        return fit

    def find(montage, detection, *args, **kwargs):
        candidate, fiducial, folded = real_find(montage, detection, *args, **kwargs)
        if getattr(current, "tp", None) == "T1":
            return None, {**fiducial, "position_um": None, "reason": "refused for the test"}, None
        return candidate, fiducial, folded

    def locate(tp, *args, **kwargs):
        current.tp = tp
        return real_locate(tp, *args, **kwargs)

    monkeypatch.setattr(nd, "detect_sites", detect)
    monkeypatch.setattr(nr, "_find_square", find)
    monkeypatch.setattr(nr, "locate_timepoint", locate)


def _identity(outcome, order=tuple(POSES)):
    montages = {tp: np.asarray(located.montage.image) for tp, located in outcome.located.items()
                if located.montage is not None}
    return ni.check_well({"timepoints": outcome.timepoints}, outcome.sites, montages, list(order))


def _run(record, images):
    return nr.run_well("A01", record, list(POSES), lambda tp, tile: images[tp].get(tile), SETTINGS,
                       segmenter=threshold_segmenter())


def test_a_timepoint_anchored_a_step_off_and_put_right_by_the_run_lands_on_itself(well, monkeypatch):
    record, images = well
    _t1_misses_its_square_and_its_centre_is_a_step_off(monkeypatch)
    outcome = _run(record, images)
    assert outcome.timepoints["T1"]["origin_method"] == "array_centre"
    assert outcome.timepoints["T1"].get("origin_shift")            # the run moved it onto T0's frame
    result = _identity(outcome)
    assert result["reference"] == "T0"
    t1 = result["timepoints"]["T1"]
    # Every address lands on itself. The fitted rim this test moved a whole pitch is what the check's second
    # landmark reads, so it withholds the confirmation, and says why: a rim that far off cannot be told from a
    # square that moved against the array.
    assert t1["failing_count"] == 0 and t1["max_pitches"] < 0.15 and t1["compared"] > 200
    assert t1["centre_agreement_pitches"] == pytest.approx(1.0, abs=0.1)
    assert t1["verdict"] == ni.INCONCLUSIVE and "fitted centre" in t1["reason"]


def test_with_the_run_s_repair_disabled_the_check_catches_the_step(well, monkeypatch):
    """The mutation: ``_settle_steps`` does nothing, so T1's rows carry its neighbours' addresses. Every check the
    run itself makes is built from the same address sets; this one registers the montages on T0's square."""
    record, images = well
    _t1_misses_its_square_and_its_centre_is_a_step_off(monkeypatch)
    monkeypatch.setattr(nr, "_settle_steps", lambda outcome, anchored, kept=None: (list(anchored), [], {}))
    outcome = _run(record, images)
    assert not outcome.timepoints["T1"].get("origin_shift")
    t1 = _identity(outcome)["timepoints"]["T1"]
    assert t1["verdict"] == ni.CONTRADICTED
    assert t1["diagnosis"]["kind"] == "step"
    assert sorted(abs(v) for v in t1["diagnosis"]["step"]) == [0, 1]


# ── E9: through process_microscopy.main ──────────────────────────────────────
SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]


def test_a_run_writes_a_confirmed_identity_sidecar(tmp_path, monkeypatch, well):
    import pipeline.suspend as _suspend
    import process_microscopy as pm
    from pipeline import nanowell_measure as nm
    from pipeline import well_skip
    from util.acquisition_record import write_record_file

    record, images = well
    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    tif = tmp_path / "frame.tif"
    with zipfile.ZipFile(plate / "in" / "A01.zip", "w") as zf:
        for tp, tiles in images.items():
            for fov, image in tiles.items():
                for channel, frame in (("PHASE", image), ("GFP", image * 0 + 10.0)):
                    imwrite(str(tif), frame.astype("float32"))
                    zf.write(tif, f"exp_{channel}_A01_{fov}_{tp}.tif")
    write_record_file(plate / "in" / "acquisition.json", record)

    def serial(*, well_zips, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix, schema,
               sep, incremental, incremental_prior_dir, settings_key, force, force_wells=None, **_ignored):
        for label, zip_path in well_zips:
            pm.process_well_zip_task(label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens,
                                     csv_prefix, schema, sep, well_skip.incremental_for_well(incremental, label, set()),
                                     incremental_prior_dir, settings_key, force)
        return []

    monkeypatch.setattr(pm, "process_well_zips", serial)
    monkeypatch.setattr(nm, "cellpose_segmenter", lambda *a, **k: threshold_segmenter())
    monkeypatch.setattr(_suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(_suspend, "watch_parent", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", [
        "process_microscopy.py", "--input_dir", str(plate / "in"), "--output_dir", str(plate / "out"),
        "--phase_token", "PHASE", "--fluor_tokens", "GFP", "--filename_schema", ":".join(SCHEMA), "--workers", "1",
        "--background", "none", "--flatfield", "none", "--no_auto_threshold", "--nanowell_segment_on", "rim",
        "--no_run_check", "--nanowell_format", "100um"])
    pm.main()
    out = plate / "out"
    payload = json.loads((out / "nanowells" / "A01_identity.json").read_text())
    assert payload["reference"] == "T0" and payload["inputs"] == ni.inputs_digest(out, "A01")
    assert ni.verdicts(out, "A01", ["T0", "T1"]) == {
        "T0": (ni.REFERENCE, "the reference timepoint"), "T1": (ni.CONFIRMED, payload["timepoints"]["T1"]["reason"])}


# ── U9a / U9b: a tile the stage recorded past half a pitch off ──────────────
LAYOUT_POSES = {"T0": (7.0, (-600.0, -600.0), 1), "T1": (8.0, (-560.0, -630.0), 2), "T2": (6.5, (-620.0, -580.0), 3)}


def _three_timepoints(cells):
    """Three timepoints of a 2 x 2 well with the central square inside F0 alone (moved off every overlap), so only
    what *cells* puts in the overlaps can break the lattice there; at T1 the stage recorded F1 0.62 pitch right."""
    from tests._nanowell_run_fixture import PITCH, PX

    record, images = None, {}
    for tp, (rotation, shift, seed) in LAYOUT_POSES.items():
        rendered, rec = build_well(timepoint=tp, seed=seed, rotation=rotation, array_shift_um=shift, cells=cells)
        images[tp] = rendered.images
        if record is None:
            record = rec
        else:
            record["timepoints"].update(rec["timepoints"])
            record["tiles"] += rec["tiles"]
    for tile in record["tiles"]:
        if tile["timepoint"] == "T1" and tile["field"] == "F1":
            tile["mosaic_xy_px"] = [tile["mosaic_xy_px"][0] + 0.62 * PITCH / PX, tile["mosaic_xy_px"][1]]
    return record, images


def _layout_run(record, images):
    return nr.run_well("A01", record, list(LAYOUT_POSES), lambda tp, tile: images[tp].get(tile), SETTINGS,
                       segmenter=threshold_segmenter())


def _f1_from_f0(outcome, tp):
    positions = outcome.located[tp].positions
    return np.subtract(positions["F1"], positions["F0"])


def test_a_tile_a_step_off_at_one_timepoint_is_put_right_by_the_others(monkeypatch):
    """The overlaps hold bare lattice, so T1's F0-F1 step comes from the stage and lands a pitch off. T0 and T2 agree
    on where F1 sits; T1 is registered again with their layout, and its addresses then land on themselves."""
    from pipeline import tile_register
    from tests._nanowell_run_fixture import PITCH

    monkeypatch.setattr(tile_register, "STEP_MIN_PEAK", float("inf"))     # content decides nothing here
    record, images = _three_timepoints(cells=0)
    outcome = _layout_run(record, images)
    t1 = outcome.timepoints["T1"]
    assert "tile_layout" in t1, "T1 was not registered again"
    disagreed = {d["pair"]: d["um"] for d in t1["tile_layout"]["disagreed"]}
    assert sorted(disagreed) == ["F0-F1", "F1-F2", "F1-F3"]            # every pair F1 is in, and only those
    assert all(abs(um - PITCH) < 0.05 * PITCH for um in disagreed.values())
    assert t1["tile_layout"]["compared_with"] == ["T0", "T2"]
    assert t1["tile_layout"]["still_disagree"] == []
    assert np.linalg.norm(_f1_from_f0(outcome, "T1") - _f1_from_f0(outcome, "T0")) < 0.1 * PITCH
    steps = {s["pair"]: s["from"] for s in t1["registration"]["steps"]}
    assert steps["F0-F1"] == tile_register.STEP_TIMEPOINTS
    assert "tile_layout" not in outcome.timepoints["T0"] and "tile_layout" not in outcome.timepoints["T2"]
    result = _identity(outcome, LAYOUT_POSES)
    assert result["timepoints"]["T1"]["verdict"] in ni.GOOD, result["timepoints"]["T1"]["reason"]


def test_cells_in_the_overlap_and_the_other_timepoints_put_the_tile_right_together():
    """With cells in the overlaps, content settles T1's F0-F1 across the stage's 0.62-pitch error, but not every pair
    F1 is in: F1-F3 leads by 0.48 where its noise asks for 0.52. The loop then cannot close, the first registration
    falls back to the stage's whole-step layout, and the other timepoints put F1 right, with content's own decision
    kept in the relocated registration. Content alone settling a tile is ``test_tile_register``'s, on wider overlaps."""
    from pipeline import tile_register
    from tests._nanowell_run_fixture import PITCH

    record, images = _three_timepoints(cells=600)
    outcome = _layout_run(record, images)
    t1 = outcome.timepoints["T1"]
    assert "tile_layout" in t1 and t1["tile_layout"]["still_disagree"] == []
    assert {s["pair"]: s["from"] for s in t1["registration"]["steps"]}["F0-F1"] == tile_register.STEP_CONTENT
    assert np.linalg.norm(_f1_from_f0(outcome, "T1") - _f1_from_f0(outcome, "T0")) < 0.1 * PITCH
    assert _identity(outcome, LAYOUT_POSES)["timepoints"]["T1"]["verdict"] in ni.GOOD


def test_a_kept_timepoint_s_layout_takes_part_in_the_agreement(monkeypatch):
    """An incremental run adding T0 and T1 to a well whose T2 an earlier run kept: two new timepoints cannot say which
    of them is wrong, the kept one's tile positions can."""
    from pipeline import tile_register
    from tests._nanowell_run_fixture import PITCH

    monkeypatch.setattr(tile_register, "STEP_MIN_PEAK", float("inf"))
    record, images = _three_timepoints(cells=0)
    kept = nr.run_well("A01", record, ["T2"], lambda tp, tile: images[tp].get(tile), SETTINGS,
                       segmenter=threshold_segmenter()).located["T2"].positions
    alone = nr.run_well("A01", record, ["T0", "T1"], lambda tp, tile: images[tp].get(tile), SETTINGS,
                        segmenter=threshold_segmenter())
    assert "tile_layout" not in alone.timepoints["T1"]                 # two timepoints: no agreement to be had
    together = nr.run_well("A01", record, ["T0", "T1"], lambda tp, tile: images[tp].get(tile), SETTINGS,
                           segmenter=threshold_segmenter(), kept_layouts={"T2": kept})
    assert "tile_layout" in together.timepoints["T1"], "T1 was not registered again"
    assert together.timepoints["T1"]["tile_layout"]["compared_with"] == ["T0", "kept:T2"]
    assert np.linalg.norm(_f1_from_f0(together, "T1") - _f1_from_f0(together, "T0")) < 0.1 * PITCH


def test_a_corner_too_small_to_judge_never_outvotes_the_edges():
    """Found by the end-to-end lane (tracker U9a): on this well the diagonal F0-F3 shares a corner of three square
    pitches. Each candidate scored over its own shared region let the wrong one a step closer be judged on a larger
    patch, where a painted cell sat one lattice vector from another (0.36 against the true corner's 0.08); content
    took it, the loop could not close, and the solve put every tile tens of micrometres astray, so the timepoint found
    no lattice at all. Every candidate is now judged on one region, against the noise of the others."""
    from tests.test_nanowell_timepoint_frames import _painted_well

    record, images = _painted_well({"T1": False}, {"T1": 3})
    tiles = nr._images_for(record, "A01", "T1", lambda tp, tile: images[tp].get(tile))
    located = nr.locate_timepoint("T1", record, "A01", tiles, SETTINGS)
    assert located.status == nr.OK
    rows = {t["tile"]: (t["left_um"], t["top_um"]) for t in located.grid["tiles"]}
    origin = np.asarray(rows["F0"])
    for tile, expected in (("F1", (2709.0, 0.0)), ("F2", (0.0, 2709.0)), ("F3", (2709.0, 2709.0))):
        assert np.hypot(*(np.asarray(rows[tile]) - origin - expected)) < 1.0, tile
    assert located.grid["registration_warnings"] == []
