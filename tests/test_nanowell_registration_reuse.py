"""A nanowell run reuses an earlier run's tile registration, and only when registering again would give the same.

The feature exists so a plate can be analysed a second time with other segmentation or measurement settings without
registering every tile again: ``--nanowell_registration_from <earlier output folder>`` (the Analyze form's *Reuse
registration* field), or the output folder's own earlier grid record when a plate is re-run in place. The stored
positions are reused only under ``nanowell_run.registration_key``, a digest of everything registration reads.

These run ``process_microscopy.main`` on rendered tiles, through the same serial harness as ``test_nanowell_cli``:
reuse is decided in ``nanowell_task.process_well_zip`` from files on disk, so only the real entry point exercises
the order the app runs it in (the grid record is read before the run rewrites it).
"""

from __future__ import annotations

import csv
import json
import sys
import zipfile

import numpy as np
import pytest
from tifffile import imwrite

import pipeline.suspend as _suspend
import pipeline.tile_register as tile_register
import process_microscopy as pm
from pipeline import nanowell_measure as nm
from tests._nanowell_run_fixture import build_well, paint_cell, threshold_segmenter
from util.acquisition_record import write_record_file

SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]
WELL = "A01"


def _write_plate(root, rendered, record, *, nudge_tile=None):
    """The plate's ``in/`` folder. *nudge_tile* brightens one tile's corner: the same plate re-exported with one
    image corrected, which the acquisition record cannot tell apart."""
    plate = root / "plate"
    (plate / "in").mkdir(parents=True)
    tif = root / "frame.tif"
    with zipfile.ZipFile(plate / "in" / f"{WELL}.zip", "w") as zf:
        for fov, image in rendered.images.items():
            frame = np.array(image, dtype="float32")
            if fov == nudge_tile:
                frame[:20, :20] += 0.5
            for channel, pixels in (("PHASE", frame), ("GFP", frame * 0 + 10.0)):
                imwrite(str(tif), pixels)
                zf.write(tif, f"exp_{channel}_{WELL}_{fov}_0h.tif")
    write_record_file(plate / "in" / "acquisition.json", record)
    return plate


def _run(monkeypatch, plate, out_name, *extra):
    from pipeline import well_skip

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
    monkeypatch.setattr(nm, "cellpose_segmenter", lambda *a, **k: threshold_segmenter())
    monkeypatch.setattr(_suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(_suspend, "watch_parent", lambda *a, **k: None)
    out = plate / out_name
    argv = ["process_microscopy.py", "--input_dir", str(plate / "in"), "--output_dir", str(out),
            "--phase_token", "PHASE", "--fluor_tokens", "GFP", "--filename_schema", ":".join(SCHEMA), "--workers", "1",
            "--background", "none", "--flatfield", "none", "--no_auto_threshold", "--no_run_check",
            "--nanowell_format", "100um", *extra]
    monkeypatch.setattr(sys, "argv", argv)
    pm.main()
    return out


def _block(out) -> dict:
    return json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())["timepoints"]["0h"]


def _sites(out) -> list[dict]:
    return list(csv.DictReader((out / "nanowells" / f"{WELL}.csv").open()))


class _CountingRegister:
    """``tile_register.register_tiles`` with a call count, so a test can say whether registration ran at all."""

    def __init__(self, monkeypatch):
        self.calls = 0
        real = tile_register.register_tiles

        def counting(*args, **kwargs):
            self.calls += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(tile_register, "register_tiles", counting)


@pytest.fixture(scope="module")
def rendered_well():
    rendered, record = build_well(timepoint="0h")
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    paint_cell(rendered, truth[(1, 2)], dx_um=3.0, dy_um=-2.0)
    return rendered, record


def test_a_second_analysis_in_another_folder_reuses_the_first_runs_registration_and_matches_a_fresh_one(
        tmp_path, monkeypatch, rendered_well):
    rendered, record = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    counter = _CountingRegister(monkeypatch)
    first = _run(monkeypatch, plate, "out1")
    assert counter.calls == 1
    assert _block(first)["registration"]["reused_from"] is None

    # Other segmentation settings, into a new folder, named as the source: registration does not run again.
    second = _run(monkeypatch, plate, "out2", "--nanowell_cell_diameter_um", "11",
                  "--nanowell_registration_from", str(first))
    assert counter.calls == 1
    reused = _block(second)
    assert reused["registration"]["reused_from"] == str(first)
    assert reused["tiles"] == _block(first)["tiles"]

    # The same settings again without the source register afresh, and give the same positions and the same rows:
    # reusing changed nothing but the time taken.
    fresh = _run(monkeypatch, plate, "out3", "--nanowell_cell_diameter_um", "11")
    assert counter.calls == 2
    assert _block(fresh)["registration"]["reused_from"] is None
    assert _block(fresh)["tiles"] == reused["tiles"]
    assert _block(fresh)["registration"]["key"] == reused["registration"]["key"]
    assert _sites(fresh) == _sites(second)
    # The source folder is not a setting of the results: the sidecar's nanowell settings do not name it.
    info = json.loads((second / "pipeline_info.json").read_text())
    assert "registration_from" not in info["nanowell"]["settings"]


def test_re_running_a_plate_in_place_with_other_segmentation_keeps_its_registration(
        tmp_path, monkeypatch, rendered_well):
    rendered, record = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    counter = _CountingRegister(monkeypatch)
    out = _run(monkeypatch, plate, "out")
    tiles = _block(out)["tiles"]
    # Other settings change the well's settings digest, so the well is recomputed; its registration is not.
    _run(monkeypatch, plate, "out", "--nanowell_cell_diameter_um", "11")
    assert counter.calls == 1
    assert _block(out)["registration"]["reused_from"] == str(out)
    assert _block(out)["tiles"] == tiles


def test_a_corrected_image_is_registered_again_even_with_the_same_record(tmp_path, monkeypatch, rendered_well):
    rendered, record = rendered_well
    first = _run(monkeypatch, _write_plate(tmp_path / "a", rendered, record), "out")
    counter = _CountingRegister(monkeypatch)
    # The same record, one tile's pixels changed: the stored registration was made from other images.
    plate = _write_plate(tmp_path / "b", rendered, record, nudge_tile=sorted(rendered.images)[0])
    second = _run(monkeypatch, plate, "out", "--nanowell_registration_from", str(first))
    assert counter.calls == 1
    assert _block(second)["registration"]["reused_from"] is None
    assert _block(second)["registration"]["key"] != _block(first)["registration"]["key"]


def test_a_folder_that_is_not_a_nanowell_run_is_refused_by_name(tmp_path, monkeypatch, rendered_well):
    rendered, record = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    (tmp_path / "elsewhere").mkdir()
    with pytest.raises(SystemExit):
        _run(monkeypatch, plate, "out", "--nanowell_registration_from", str(tmp_path / "elsewhere"))
    assert not (plate / "out" / "nanowells").exists()
