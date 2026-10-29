"""The identity check over the real pipeline's output (NANOWELL_IDENTITY_TRACKER E9, E10).

``test_nanowell_identity.py`` injects each fault into tables built from the renderer's ground truth. This file
runs the real detection and addressing on rendered tiles and checks what the run actually wrote:

* a well whose second timepoint could not read its square and was anchored on its fitted array centre a step off
  is put right by the run (``nanowell_run._settle_steps``), and the check confirms it — and with that repair
  disabled, the check is what catches the step, naming it (E10, the mutation that proves the check bites on
  the pipeline's own output rather than on hand-built tables);
* ``process_microscopy.main`` writes ``nanowells/<well>_identity.json`` for a two-timepoint well, confirmed, and
  Review-side :func:`verdicts` reads it back (E9).
"""
from __future__ import annotations

import json
import sys
import zipfile
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
    current = {"tp": None}

    def detect(*args, **kwargs):
        fit = real_detect(*args, **kwargs)
        if current["tp"] == "T1" and fit.ok and fit.boundary_centre_um is not None:
            fit = replace(fit, boundary_centre_um=tuple(np.asarray(fit.boundary_centre_um) + np.asarray(fit.fit.a1)))
        return fit

    def find(montage, detection, *args, **kwargs):
        candidate, fiducial, folded = real_find(montage, detection, *args, **kwargs)
        if current["tp"] == "T1":
            return None, {**fiducial, "position_um": None, "reason": "refused for the test"}, None
        return candidate, fiducial, folded

    def locate(tp, *args, **kwargs):
        current["tp"] = tp
        return real_locate(tp, *args, **kwargs)

    monkeypatch.setattr(nd, "detect_sites", detect)
    monkeypatch.setattr(nr, "_find_square", find)
    monkeypatch.setattr(nr, "locate_timepoint", locate)


def _identity(outcome):
    montages = {tp: np.asarray(located.montage.image) for tp, located in outcome.located.items()
                if located.montage is not None}
    return ni.check_well({"timepoints": outcome.timepoints}, outcome.sites, montages, list(POSES))


def _run(record, images):
    return nr.run_well("A01", record, list(POSES), lambda tp, tile: images[tp].get(tile), SETTINGS,
                       segmenter=threshold_segmenter())


def test_a_timepoint_anchored_a_step_off_and_put_right_by_the_run_is_confirmed(well, monkeypatch):
    record, images = well
    _t1_misses_its_square_and_its_centre_is_a_step_off(monkeypatch)
    outcome = _run(record, images)
    assert outcome.timepoints["T1"]["origin_method"] == "array_centre"
    assert outcome.timepoints["T1"].get("origin_shift")            # the run moved it onto T0's frame
    result = _identity(outcome)
    assert result["reference"] == "T0"
    t1 = result["timepoints"]["T1"]
    assert t1["verdict"] == ni.CONFIRMED, t1["reason"]
    assert t1["max_pitches"] < 0.15 and t1["compared"] > 200


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
