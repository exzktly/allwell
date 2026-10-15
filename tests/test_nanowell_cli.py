"""``process_microscopy.main`` in nanowell mode on rendered tiles, end to end (tracker P11).

The well pool runs serially in this process and the label-free segmenter is a deterministic
stand-in for Cellpose (the real one is exercised in the slow segmentation lane): the harness
``test_only_wells_run`` uses, for the same reasons.
"""

from __future__ import annotations

import csv
import json
import sys
import zipfile
from types import SimpleNamespace

import pytest
from tifffile import imwrite

import pipeline.suspend as _suspend
import process_microscopy as pm
from pipeline import nanowell_measure as nm
from tests._nanowell_run_fixture import build_well, paint_cell, threshold_segmenter
from util.acquisition_record import write_record_file

SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]
WELL = "A01"


def _write_plate(tmp_path, rendered, record, *, token="0h", fluorescence=None):
    """``fluorescence`` maps a fov to its GFP image; every tile gets a flat 10 when it is not given."""
    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    tif = tmp_path / "frame.tif"
    with zipfile.ZipFile(plate / "in" / f"{WELL}.zip", "w") as zf:
        for fov, image in rendered.images.items():
            for channel, frame in (("PHASE", image), ("GFP", (fluorescence or {}).get(fov, image * 0 + 10.0))):
                imwrite(str(tif), frame.astype("float32"))
                zf.write(tif, f"exp_{channel}_{WELL}_{fov}_{token}.tif")
    write_record_file(plate / "in" / "acquisition.json", record)
    return plate


def _run_main(monkeypatch, plate, *extra):
    from pipeline import well_skip

    def _serial(*, well_zips, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix,
                schema, sep, incremental, incremental_prior_dir, settings_key, force, force_wells=None, **_ignored):
        forced = well_skip._canonical_force_wells(force_wells)
        failed = []
        for label, zip_path in well_zips:
            pm.process_well_zip_task(label, zip_path, output_dir, shared_kwargs_template, nuclear_token,
                                     fluor_tokens, csv_prefix, schema, sep,
                                     well_skip.incremental_for_well(incremental, label, forced),
                                     incremental_prior_dir, settings_key, force or label in forced)
        return failed

    monkeypatch.setattr(pm, "process_well_zips", _serial)
    monkeypatch.setattr(nm, "cellpose_segmenter", lambda *a, **k: threshold_segmenter())
    monkeypatch.setattr(_suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(_suspend, "watch_parent", lambda *a, **k: None)
    argv = ["process_microscopy.py", "--input_dir", str(plate / "in"), "--output_dir", str(plate / "out"),
            "--phase_token", "PHASE", "--fluor_tokens", "GFP", "--filename_schema", ":".join(SCHEMA), "--workers", "1",
            "--background", "none", "--flatfield", "none", "--no_auto_threshold", "--no_run_check", *extra]
    monkeypatch.setattr(sys, "argv", argv)
    pm.main()
    return plate / "out"


@pytest.fixture(scope="module")
def rendered_well():
    rendered, record = build_well(timepoint="0h")
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    paint_cell(rendered, truth[(1, 2)], dx_um=3.0, dy_um=-2.0)
    return rendered, record, truth


def test_a_nanowell_run_writes_the_three_tables_and_the_cell_lands_at_its_address(tmp_path, monkeypatch, rendered_well):
    rendered, record, truth = rendered_well
    glow = SimpleNamespace(images={f: image * 0 + 10.0 for f, image in rendered.images.items()},
                           true_left_top_um=rendered.true_left_top_um)
    paint_cell(glow, truth[(1, 2)], dx_um=3.0, dy_um=-2.0, amount=200.0)
    plate = _write_plate(tmp_path, rendered, record, fluorescence=glow.images)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um")
    sites = list(csv.DictReader((out / "nanowells" / f"{WELL}.csv").open()))
    assert len(sites) > 600 and {r["timepoint"] for r in sites} == {"0h"}
    by_address = {(int(r["nw_q"]), int(r["nw_r"])): r for r in sites if r["nw_q"]}
    assert set(truth) <= set(by_address)
    assert by_address[(1, 2)]["n_cells"] == "1" and by_address[(1, 2)]["state"] == "measured"
    assert by_address[(0, 1)]["n_cells"] == "0"
    cells = list(csv.DictReader((out / f"measurements_{WELL}.csv").open()))
    assert len(cells) == 1
    assert (cells[0]["nw_q"], cells[0]["nw_r"], cells[0]["well"], cells[0]["timepoint"]) == ("1", "2", WELL, "0h")
    assert cells[0]["fov"] in {"F0", "F1", "F2", "F3"}
    assert float(cells[0]["GFP_mean_intensity"]) > 100.0                      # the glow painted into that cell
    assert float(by_address[(1, 2)]["GFP_integrated"]) > 1000.0
    assert abs(float(by_address[(0, 1)]["GFP_mean_bg"])) < 1.0                # an empty site, over the background
    grid = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())
    assert grid["timepoints"]["0h"]["status"] == "ok" and grid["scheme_version"] == 1
    info = json.loads((out / "pipeline_info.json").read_text())
    assert info["nanowell"]["settings"]["plate_format"] == "100um"
    assert info["acquisition"]["0h"]["tile_size_px"] == {"x": 700, "y": 700}


def test_nanowell_mode_without_a_record_names_what_is_missing(tmp_path, monkeypatch, rendered_well):
    rendered, record, _ = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    (plate / "in" / "acquisition.json").unlink()
    with pytest.raises(Exception, match="acquisition record"):
        _run_main(monkeypatch, plate, "--nanowell_format", "100um")


@pytest.mark.parametrize("flags, message", [
    (["--nanowell_cell_diameter_um", "0"], "positive"),
    (["--nanowell_margin_um", "-1"], "zero or more"),
    (["--nanowell_square_edge_deg", "60"], "-45 and 45"),
])
def test_nanowell_options_are_validated_with_a_message(tmp_path, monkeypatch, rendered_well, capsys, flags, message):
    rendered, record, _ = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, plate, "--nanowell_format", "100um", *flags)
    assert message in capsys.readouterr().err


def test_nanowell_mode_needs_a_phase_channel(tmp_path, monkeypatch, rendered_well, capsys):
    rendered, record, _ = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    monkeypatch.setattr(sys, "argv", ["process_microscopy.py", "--input_dir", str(plate / "in"),
                                      "--output_dir", str(plate / "out"), "--nanowell_format", "100um",
                                      "--fluor_tokens", "GFP"])
    with pytest.raises(SystemExit):
        pm.main()
    assert "--phase_token" in capsys.readouterr().err
