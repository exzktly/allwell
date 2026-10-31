"""``process_microscopy.main`` in nanowell mode on rendered tiles, end to end (tracker P11).

The well pool runs serially in this process and the label-free segmenter is a deterministic
stand-in for Cellpose (the real one is exercised in the slow segmentation lane): the harness
``test_only_wells_run`` uses, for the same reasons.
"""

from __future__ import annotations

import csv
import gzip
import json
import sys
import zipfile
from types import SimpleNamespace

import numpy as np
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


def _run_main(monkeypatch, plate, *extra, run_check=False, segmenter_factory=None):
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
    monkeypatch.setattr(nm, "cellpose_segmenter", segmenter_factory or (lambda *a, **k: threshold_segmenter()))
    monkeypatch.setattr(_suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(_suspend, "watch_parent", lambda *a, **k: None)
    argv = ["process_microscopy.py", "--input_dir", str(plate / "in"), "--output_dir", str(plate / "out"),
            "--phase_token", "PHASE", "--fluor_tokens", "GFP", "--filename_schema", ":".join(SCHEMA), "--workers", "1",
            "--background", "none", "--flatfield", "none", "--no_auto_threshold",
            # The stand-in segmenter thresholds the rim-divided image (flat 1.0 between cells), so it is given that.
            *([] if "--nanowell_segment_on" in extra else ["--nanowell_segment_on", "rim"]),
            *([] if run_check else ["--no_run_check"]), *extra]
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
    assert len(sites) > 200 and {r["timepoint"] for r in sites} == {"0h"}
    by_address = {(int(r["nw_q"]), int(r["nw_r"])): r for r in sites if r["nw_q"]}
    assert set(truth) <= set(by_address)
    assert by_address[(1, 2)]["n_cells"] == "1" and by_address[(1, 2)]["state"] == "measured"
    assert by_address[(0, 1)]["n_cells"] == "0"
    assert not (out / f"measurements_{WELL}.csv").exists()                   # a finished well is gzipped (D11)
    cells = list(csv.DictReader(gzip.open(out / f"measurements_{WELL}.csv.gz", "rt", newline="")))
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
    (["--nanowell_cell_diameter_um", "inf"], "positive number of micrometres"),   # roll-up review X20
    (["--nanowell_margin_um", "inf"], "zero or more"),
    (["--nanowell_square_edge_deg", "60"], "-45 and 45"),
    (["--nanowell_occluded_below", "0"], "above 0 and at most 1"),
    (["--nanowell_clipped_below", "1.5"], "above 0 and at most 1"),
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


# ── incremental ──────────────────────────────────────────────────────────────
def _two_timepoint_plate(tmp_path, *, with_second=True, cell=False):
    """*cell* paints one cell into nanowell (1, 2) at each timepoint, so the well's cell CSV has rows to keep."""
    first, record = build_well(rows=1, cols=1, tile=(900, 900), timepoint="0h", radius_fraction=0.26)
    if cell:
        paint_cell(first, dict(zip(first.array.addresses, first.sites_um))[(1, 2)], dx_um=3.0, dy_um=-2.0)
    items = [("0h", first)]
    if with_second:
        second, second_record = build_well(rows=1, cols=1, tile=(900, 900), rotation=9.0, array_shift_um=(120.0, -80.0),
                                           seed=2, timepoint="24h", radius_fraction=0.26)
        if cell:
            paint_cell(second, dict(zip(second.array.addresses, second.sites_um))[(1, 2)], dx_um=3.0, dy_um=-2.0)
        record["timepoints"].update(second_record["timepoints"])
        record["tiles"] += second_record["tiles"]
        items.append(("24h", second))
    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    tif = tmp_path / "frame.tif"
    with zipfile.ZipFile(plate / "in" / f"{WELL}.zip", "w") as zf:
        for token, rendered in items:
            for fov, image in rendered.images.items():
                for channel, frame in (("PHASE", image), ("GFP", image * 0 + 10.0)):
                    imwrite(str(tif), frame.astype("float32"))
                    zf.write(tif, f"exp_{channel}_{WELL}_{fov}_{token}.tif")
    write_record_file(plate / "in" / "acquisition.json", record)
    return plate, record, dict(items)


@pytest.fixture
def located_calls(monkeypatch):
    """Which timepoints each stage was asked for, recorded without changing what runs."""
    from pipeline import nanowell_run as nr

    calls = {"locate": [], "measure": []}
    real_locate, real_measure = nr.locate_timepoint, nr.measure_timepoint
    monkeypatch.setattr(nr, "locate_timepoint", lambda tp, *a, **k: (calls["locate"].append(tp), real_locate(tp, *a, **k))[1])
    monkeypatch.setattr(nr, "measure_timepoint", lambda located, *a, **k: (calls["measure"].append(located.timepoint), real_measure(located, *a, **k))[1])
    return calls


def _cells(out):
    return list(csv.DictReader(gzip.open(out / f"measurements_{WELL}.csv.gz", "rt", newline="")))


def _rows(out, name):
    return list(csv.DictReader((out / "nanowells" / name).open()))


def test_an_incremental_run_adds_only_the_new_timepoint_onto_the_same_roster(tmp_path, monkeypatch, located_calls):
    plate, record, rendered = _two_timepoint_plate(tmp_path, with_second=False, cell=True)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--fluor_tokens", "GFP")
    first_rows = _rows(out, f"{WELL}.csv")
    first_cells = _cells(out)
    assert [c["timepoint"] for c in first_cells] == ["0h"]
    assert located_calls["measure"] == ["0h"]

    # The plate is imaged again: a second timepoint joins the zip and the record.
    plate2, record2, _ = _two_timepoint_plate(tmp_path / "second", cell=True)
    (plate / "in" / f"{WELL}.zip").write_bytes((plate2 / "in" / f"{WELL}.zip").read_bytes())
    write_record_file(plate / "in" / "acquisition.json", record2)
    located_calls["locate"].clear(), located_calls["measure"].clear()
    _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--incremental")
    assert located_calls["locate"] == ["24h"] and located_calls["measure"] == ["24h"]      # 0h was not touched
    rows = _rows(out, f"{WELL}.csv")
    assert [r for r in rows if r["timepoint"] == "0h"] == first_rows                    # kept verbatim
    # The kept timepoint's cells too, read back from the gzipped well CSV the first run left (D11).
    cells = _cells(out)
    assert [c for c in cells if c["timepoint"] == "0h"] == first_cells and {c["timepoint"] for c in cells} == {
        "0h", "24h"}
    by_tp = {tp: {(r["nw_q"], r["nw_r"]) for r in rows if r["timepoint"] == tp} for tp in ("0h", "24h")}
    assert by_tp["0h"] == by_tp["24h"] and len(by_tp["0h"]) > 100                         # one roster
    grid = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())
    assert set(grid["timepoints"]) == {"0h", "24h"} and grid["timepoints"]["0h"]["status"] == "ok"

    located_calls["locate"].clear(), located_calls["measure"].clear()
    _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--incremental")
    assert located_calls["locate"] == [] and located_calls["measure"] == []                 # up to date


def test_an_incremental_run_of_the_empty_reference_well_saves_the_new_timepoint(tmp_path, monkeypatch):
    """Adding a timepoint to a plate whose empty reference well was measured before (tracker H1). The kept
    timepoint's site rows come back from ``nanowells/<well>.csv`` as text, and the reference's verdict was taken over
    them beside the new timepoint's numbers: ``'0' > 0`` raised, the well failed, and every other well then read the
    new timepoint with no confluence. Both timepoints must be judged, from the counts, and saved."""
    from pipeline import nanowell_reference as nref

    plate, _record, _ = _two_timepoint_plate(tmp_path, with_second=False)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--nanowell_empty_well", WELL)
    assert set(nref.read_index(out)["timepoints"]) == {"0h"}

    plate2, record2, _ = _two_timepoint_plate(tmp_path / "second")
    (plate / "in" / f"{WELL}.zip").write_bytes((plate2 / "in" / f"{WELL}.zip").read_bytes())
    write_record_file(plate / "in" / "acquisition.json", record2)
    _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--incremental", "--nanowell_empty_well", WELL)
    saved = nref.read_index(out)["timepoints"]
    assert {tp: v["status"] for tp, v in saved.items()} == {"0h": nref.EMPTY, "24h": nref.EMPTY}
    assert saved["0h"]["nanowells_counted"] > 100 and saved["0h"]["loaded_share"] == 0.0


def test_an_incremental_run_over_a_csv_without_timepoint_hours_writes_it_for_every_row(tmp_path, monkeypatch):
    """A CSV from a run before the per-cell table carried ``timepoint_hours``: the kept timepoint's rows are read
    back without it and written ahead of the new timepoint's, in one table whose header comes from its first row."""
    plate, _record, _ = _two_timepoint_plate(tmp_path, with_second=False, cell=True)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--fluor_tokens", "GFP")
    old = [{k: v for k, v in row.items() if k != "timepoint_hours"} for row in _cells(out)]
    assert old and "timepoint_hours" not in old[0]
    with gzip.open(out / f"measurements_{WELL}.csv.gz", "wt", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(old[0]))
        writer.writeheader()
        writer.writerows(old)

    plate2, record2, _ = _two_timepoint_plate(tmp_path / "second", cell=True)
    (plate / "in" / f"{WELL}.zip").write_bytes((plate2 / "in" / f"{WELL}.zip").read_bytes())
    write_record_file(plate / "in" / "acquisition.json", record2)
    _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--incremental")
    cells = _cells(out)
    assert {(c["timepoint"], float(c["timepoint_hours"])) for c in cells} == {("0h", 0.0), ("24h", 24.0)}
    header = list(cells[0])
    assert header[header.index("timepoint") + 1] == "timepoint_hours"


def test_a_changed_setting_or_a_changed_record_recomputes_the_timepoint(tmp_path, monkeypatch, located_calls):
    plate, record, _ = _two_timepoint_plate(tmp_path)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--fluor_tokens", "GFP")
    assert sorted(located_calls["measure"]) == ["0h", "24h"]
    located_calls["locate"].clear(), located_calls["measure"].clear()
    _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--incremental", "--nanowell_margin_um", "5")
    assert sorted(located_calls["measure"]) == ["0h", "24h"]                    # the setting is in the reuse key
    located_calls["locate"].clear(), located_calls["measure"].clear()
    for tile in record["tiles"]:                                                # a tile's recorded position moves
        if tile["timepoint"] == "0h" and tile["field"] == "F0":
            tile["mosaic_xy_px"] = [tile["mosaic_xy_px"][0] + 0.5, tile["mosaic_xy_px"][1]]
    write_record_file(plate / "in" / "acquisition.json", record)
    _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--incremental", "--nanowell_margin_um", "5")
    assert located_calls["measure"] == ["0h"]                                    # only the timepoint whose record changed
    assert (out / f"{WELL}_out.zip").stat().st_size > 0


def test_the_completeness_check_counts_a_nanowell_well_in_timepoints_and_finds_it_whole(tmp_path, monkeypatch, rendered_well):
    rendered, record, _ = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", run_check=True)
    info = json.loads((out / "pipeline_info.json").read_text())
    well = info["run_check"]["wells"][WELL]
    assert (well["intent"], well["covered_csv"], well["missing_count"], well["health"]) == (1, 1, 0, "complete")


def test_a_well_with_no_cells_still_has_its_cell_csv_so_it_reads_as_finished(tmp_path, monkeypatch):
    rendered, record = build_well(timepoint="0h")            # nothing painted: every nanowell is empty
    plate = _write_plate(tmp_path, rendered, record)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", run_check=True)
    lines = gzip.open(out / f"measurements_{WELL}.csv.gz", "rt").read().splitlines()   # gzipped, even empty
    assert len(lines) == 1 and lines[0].startswith("well,fov,timepoint,timepoint_hours,nucleus_id,area_px,PHASE_mean_intensity,GFP_mean_intensity,nw_q")
    sites = _rows(out, f"{WELL}.csv")
    assert sites and all(r["n_cells"] == "0" for r in sites if r["state"] == "empty")
    info = json.loads((out / "pipeline_info.json").read_text())
    assert info["run_check"]["wells"][WELL]["health"] == "complete"


def test_an_incremental_run_compares_the_new_timepoint_s_template_with_the_kept_earliest(tmp_path, monkeypatch):
    """Tracker C12, in the order the app runs it: 0h is analysed, then an incremental run adds 24h. The earliest
    template is 0h's, which the second run never measures, so the comparison has to reach the kept grid record
    (a run-local "first one measured" compared 24h with nothing, and a third timepoint with 24h)."""
    import math

    plate, _record, _ = _two_timepoint_plate(tmp_path, with_second=False)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--fluor_tokens", "GFP")
    first = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())["timepoints"]["0h"]["template"]
    assert first["source"] == "own" and first["floor_ratio"] > 0 and "drift_vs_earliest" not in first

    plate2, record2, _ = _two_timepoint_plate(tmp_path / "second")
    (plate / "in" / f"{WELL}.zip").write_bytes((plate2 / "in" / f"{WELL}.zip").read_bytes())
    write_record_file(plate / "in" / "acquisition.json", record2)
    _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--incremental")
    blocks = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())["timepoints"]
    later = blocks["24h"]["template"]
    assert later["earliest_timepoint"] == "0h"
    assert later["drift_vs_earliest"] == pytest.approx(abs(math.log(later["floor_ratio"] / first["floor_ratio"])))
    assert "suspect" not in later and "earliest_timepoint" not in blocks["0h"]["template"]


def test_an_incremental_timepoint_too_crowded_for_its_own_template_falls_back_to_the_kept_earliest(
        tmp_path, monkeypatch):
    """Tracker C16: 24h, added by an incremental run, has every nanowell crowded with cells, so too few empty
    ones to build a template. It falls back to 0h's — kept on disk by the first run, since this run never
    measures 0h — rather than building one from the one or two sites it has."""
    import numpy as np
    import tifffile

    from tests._nanowell_run_fixture import paint_cell

    plate, _record, _ = _two_timepoint_plate(tmp_path, with_second=False)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--fluor_tokens", "GFP")
    assert (out / "nanowells" / f"{WELL}_template.npz").is_file()

    second, _ = build_well(rows=1, cols=1, tile=(900, 900), rotation=9.0, array_shift_um=(120.0, -80.0),
                           seed=2, timepoint="24h", radius_fraction=0.26)
    rng = np.random.default_rng(5)
    for site in second.sites_um:
        for _ in range(6):
            paint_cell(second, site, dx_um=rng.uniform(-22, 22), dy_um=rng.uniform(-22, 22), radius_px=3.0)
    plate2, record2, _ = _two_timepoint_plate(tmp_path / "second")
    with zipfile.ZipFile(plate2 / "in" / f"{WELL}.zip") as zf:
        members = {name: zf.read(name) for name in zf.namelist() if not name.endswith("_24h.tif")}
    tif = tmp_path / "crowded.tif"
    with zipfile.ZipFile(plate / "in" / f"{WELL}.zip", "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        for fov, image in second.images.items():
            for channel, frame in (("PHASE", image), ("GFP", image * 0 + 10.0)):
                tifffile.imwrite(str(tif), frame.astype("float32"))
                zf.write(tif, f"exp_{channel}_{WELL}_{fov}_24h.tif")
    write_record_file(plate / "in" / "acquisition.json", record2)
    _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--incremental")
    later = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())["timepoints"]["24h"]["template"]
    assert later["source"] == "earliest"


# ── no acquisition record: the grid on the command line (tracker P15) ────────
_GRID = ["--nanowell_grid", "2x2", "--nanowell_scan", "top-left-row", "--nanowell_overlap_percent", "10",
         "--nanowell_pixel_size_um", "4.3"]


def test_a_dataset_without_a_record_runs_from_the_grid_it_is_given(tmp_path, monkeypatch, rendered_well):
    """The fixture's tiles F0..F3 were imaged row by row from the top left at 10 % overlap; with the record
    removed and that described on the command line, the run places, addresses and counts as it does with it."""
    from review.features.nanowell.collect import load_nanowell_data

    rendered, record, truth = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    (plate / "in" / "acquisition.json").unlink()
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", *_GRID)
    assert not (plate / "in" / "acquisition.json").exists()                    # nothing written into the input
    grid = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())
    assert grid["timepoints"]["0h"]["status"] == "ok"
    sites = {(int(r["nw_q"]), int(r["nw_r"])): r for r in csv.DictReader((out / "nanowells" / f"{WELL}.csv").open())
             if r["nw_q"]}
    assert set(truth) <= set(sites) and sites[(1, 2)]["n_cells"] == "1"
    info = json.loads((out / "pipeline_info.json").read_text())
    assert info["nanowell"]["settings"]["pattern"] == {"grid": [2, 2], "scan": "top-left-row",
                                                       "overlap_percent": 10.0, "pixel_size_um": 4.3}
    data = load_nanowell_data(out)
    assert data.acquisition["0h"]["pixel_size_um"] == {"x": 4.3, "y": 4.3}       # Review reads the pattern's
    assert data.time_source in ("label", None)


@pytest.mark.parametrize("grid, status", [("3x2", "incomplete_grid"), ("1x2", "bad_grid")])
def test_a_described_grid_the_well_s_fields_do_not_fill_is_refused_not_measured(tmp_path, monkeypatch,
                                                                                rendered_well, grid, status):
    """The fixture's four tiles under a 3 x 2 grid are a row short; under a 1 x 2 grid, two too many. Placed
    from their positions alone both look like a complete 2 x 2 grid and would be measured as one."""
    rendered, record, _ = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    (plate / "in" / "acquisition.json").unlink()
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--nanowell_grid", grid, *_GRID[2:])
    block = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())["timepoints"]["0h"]
    assert block["status"] == status


@pytest.mark.parametrize("flags, message", [
    (["--nanowell_grid", "2x2", "--nanowell_overlap_percent", "10", "--nanowell_pixel_size_um", "inf"],
     "needs --nanowell_pixel_size_um"),
    (["--nanowell_grid", "2x2", "--nanowell_pixel_size_um", "4.3"], "needs --nanowell_overlap_percent"),
    (["--nanowell_grid", "2x2", "--nanowell_overlap_percent", "10"], "needs --nanowell_pixel_size_um"),
    (["--nanowell_grid", "two by two", "--nanowell_overlap_percent", "10", "--nanowell_pixel_size_um", "4.3"],
     "is not a grid"),
    ([*_GRID[:2], "--nanowell_scan", "diagonal", *_GRID[4:]], "is not a scan pattern"),
    (["--nanowell_overlap_percent", "10"], "go with --nanowell_grid"),
])
def test_the_grid_options_are_validated_with_a_message(tmp_path, monkeypatch, rendered_well, capsys, flags, message):
    rendered, record, _ = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, plate, "--nanowell_format", "100um", *flags)
    assert message in capsys.readouterr().err


# ── a custom plate format (tracker A7) ───────────────────────────────────────
def test_a_custom_format_with_the_100um_geometry_reads_the_array_as_the_100um_format_does(
        tmp_path, monkeypatch, rendered_well):
    rendered, record, truth = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "custom", "--nanowell_diameter_um", "100",
                    "--nanowell_pitch_um", "172")
    sites = {(int(r["nw_q"]), int(r["nw_r"])): r for r in csv.DictReader((out / "nanowells" / f"{WELL}.csv").open())
             if r["nw_q"]}
    assert set(truth) <= set(sites) and sites[(1, 2)]["n_cells"] == "1"
    settings = json.loads((out / "pipeline_info.json").read_text())["nanowell"]["settings"]
    assert (settings["plate_format"], settings["diameter_um"], settings["pitch_um"]) == ("custom", 100.0, 172.0)


@pytest.mark.parametrize("flags, message", [
    (["--nanowell_format", "custom", "--nanowell_diameter_um", "100"], "needs --nanowell_diameter_um and"),
    (["--nanowell_format", "custom", "--nanowell_diameter_um", "100", "--nanowell_pitch_um", "inf"],
     "needs --nanowell_diameter_um and"),
    (["--nanowell_format", "custom", "--nanowell_diameter_um", "100", "--nanowell_pitch_um", "100"], "would touch"),
    (["--nanowell_format", "100um", "--nanowell_pitch_um", "150"], "go with --nanowell_format custom"),
])
def test_a_custom_format_is_validated_with_a_message(tmp_path, monkeypatch, rendered_well, capsys, flags, message):
    rendered, record, _ = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, plate, *flags)
    assert message in capsys.readouterr().err


def test_a_well_s_montages_are_spilled_into_its_scratch_and_go_with_it(tmp_path, monkeypatch, rendered_well):
    """Roll-up review X23, through the real task: the montages are memory maps under the well's scratch while it
    runs, the montage archive is still written from them, and nothing of them is left once the well is done."""
    from pipeline import nanowell_run as nr

    rendered, record, _ = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    seen = []
    real = nr.run_well

    def spy(*args, **kwargs):
        outcome = real(*args, **kwargs)
        seen.append((kwargs.get("spill_dir"), [type(l.montage.image) for l in outcome.located.values()]))
        return outcome

    monkeypatch.setattr(nr, "run_well", spy)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um")
    (spill, kinds), = seen
    assert spill is not None and spill.name == ".montages" and kinds == [np.memmap]
    assert not spill.exists()
    with zipfile.ZipFile(out / f"{WELL}_out.zip") as zf:
        assert f"{WELL}_0h_montage.png" in zf.namelist()


# ── an inferred geometry (tracker X35) ───────────────────────────────────────
def _square_plate(tmp_path, timepoints=("0h",), lattices=None):
    """A square lattice in a square array, turned 12 degrees, with no central square, one 900 px tile a timepoint;
    the second timepoint is the plate put back shifted and turned a little. *lattices* gives each timepoint's
    lattice instead (a plate that cannot exist, to test the refusal of one)."""
    record, items = None, []
    for k, token in enumerate(timepoints):
        rendered, rec = build_well(rows=1, cols=1, tile=(900, 900), timepoint=token, seed=1 + k,
                                   lattice=(lattices or {}).get(token, "square"),
                                   array_shape="square", rotation=12.0 + 2 * k, radius_fraction=0.3,
                                   square_centre=False, array_shift_um=(60.0 * k, -40.0 * k))
        if record is None:
            record = rec
        else:
            record["timepoints"].update(rec["timepoints"])
            record["tiles"] += rec["tiles"]
        items.append((token, rendered))
    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    tif = tmp_path / "frame.tif"
    with zipfile.ZipFile(plate / "in" / f"{WELL}.zip", "w") as zf:
        for token, rendered in items:
            for fov, image in rendered.images.items():
                for channel, frame in (("PHASE", image), ("GFP", image * 0 + 10.0)):
                    imwrite(str(tif), frame.astype("float32"))
                    zf.write(tif, f"exp_{channel}_{WELL}_{fov}_{token}.tif")
    write_record_file(plate / "in" / "acquisition.json", record)
    return plate, dict(items)


def test_an_inferred_geometry_reaches_the_run_from_the_command_line(tmp_path, monkeypatch):
    plate, rendered = _square_plate(tmp_path)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--nanowell_geometry", "infer")
    rows = _rows(out, f"{WELL}.csv")
    assert {(int(r["nw_q"]), int(r["nw_r"])) for r in rows} == set(rendered["0h"].array.addresses)
    assert {r["nw_ring"] for r in rows if (r["nw_q"], r["nw_r"]) == ("3", "-2")} == {"3"}     # square rings
    grid = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())
    block = grid["timepoints"]["0h"]
    assert (block["status"], block["lattice_kind"], block["geometry"]["shape"]) == ("ok", "square", "polygon")
    assert grid["array_radius"]["radius_um"] is None and len(grid["array_radius"]["roster"]) == len(rows)
    assert json.loads((out / "pipeline_info.json").read_text())["nanowell"]["settings"]["geometry"] == "infer"


def test_without_the_flag_the_same_square_plate_is_read_as_hexagonal_and_gets_no_addresses(tmp_path, monkeypatch):
    plate, _ = _square_plate(tmp_path)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um")
    grid = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())
    assert grid["timepoints"]["0h"]["status"] != "ok"
    assert all(not r["nw_q"] for r in _rows(out, f"{WELL}.csv"))


def test_an_incremental_inferred_run_adds_the_new_timepoint_to_the_kept_roster(tmp_path, monkeypatch, located_calls):
    from pipeline import nanowell_run as nr

    plate, _ = _square_plate(tmp_path)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--nanowell_geometry", "infer")
    first = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())["array_radius"]["roster"]
    plate2, _ = _square_plate(tmp_path / "second", timepoints=("0h", "24h"))
    (plate / "in" / f"{WELL}.zip").write_bytes((plate2 / "in" / f"{WELL}.zip").read_bytes())
    (plate / "in" / "acquisition.json").write_bytes((plate2 / "in" / "acquisition.json").read_bytes())
    given = []
    real = nr.run_well
    monkeypatch.setattr(nr, "run_well", lambda *a, **k: (given.append(k.get("fixed_roster")), real(*a, **k))[1])
    located_calls["measure"].clear()
    _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--nanowell_geometry", "infer", "--incremental")
    assert located_calls["measure"] == ["24h"] and given == [first]
    rows = _rows(out, f"{WELL}.csv")
    by_tp = {tp: {(r["nw_q"], r["nw_r"]) for r in rows if r["timepoint"] == tp} for tp in ("0h", "24h")}
    assert by_tp["0h"] == by_tp["24h"] and len(by_tp["0h"]) == len(first)
    grid = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())
    assert grid["array_radius"]["roster"] == first and grid["timepoints"]["24h"]["status"] == "ok"


def test_an_incremental_timepoint_of_another_lattice_kind_than_the_kept_ones_is_refused(tmp_path, monkeypatch):
    """Review X35-R13, through the task: the kept run's lattice kind reaches the vote (``_reference_of``)."""
    plate, _ = _square_plate(tmp_path, lattices={"0h": "hex"})
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--nanowell_geometry", "infer")
    assert json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())["timepoints"]["0h"]["lattice_kind"] == "hex"
    plate2, _ = _square_plate(tmp_path / "second", timepoints=("0h", "24h"), lattices={"0h": "hex"})
    (plate / "in" / f"{WELL}.zip").write_bytes((plate2 / "in" / f"{WELL}.zip").read_bytes())
    (plate / "in" / "acquisition.json").write_bytes((plate2 / "in" / "acquisition.json").read_bytes())
    _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--nanowell_geometry", "infer", "--incremental")
    block = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())["timepoints"]["24h"]
    assert block["status"] == "lattice_kind_mismatch" and "the earlier run kept a hex one" in block["reason"]
    assert not [r for r in _rows(out, f"{WELL}.csv") if r["timepoint"] == "24h"]


def test_a_nanowell_run_prints_the_progress_its_well_s_dial_follows(tmp_path, monkeypatch, rendered_well, caplog):
    """Reported: during a nanowell run the well's dial on Analyze's plate never moved. Driven through the real run and
    the real reader of its log: the dial starts at the well's total and climbs to it, never backwards."""
    import logging

    from services.pipeline_runner import ProgressTracker

    rendered, record, _truth = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    with caplog.at_level(logging.INFO, logger="allwell.nanowell"):
        _run_main(monkeypatch, plate, "--nanowell_format", "100um")
    reader = ProgressTracker()
    dial = [payload for record_ in caplog.records for kind, payload in reader.parse(record_.getMessage())
            if kind == "well_fov"]
    assert dial and dial[0] == (WELL, 0, 2, "step") and dial[-1] == (WELL, 2, 2, "step")   # located, then measured
    assert [p[1] for p in dial] == sorted(p[1] for p in dial)
    # Inside the measure step, the nanowells segmented so far: from none to all of them, about a tenth at a time,
    # and only between that step starting and ending — where the dial adds them to the step it is in.
    reader = ProgressTracker()
    events = [(kind, payload) for record_ in caplog.records for kind, payload in reader.parse(record_.getMessage())
              if kind in ("well_fov", "well_sites")]
    sites = [payload for kind, payload in events if kind == "well_sites"]
    assert sites and sites[0][:3] == (WELL, "0h", 0) and sites[-1][2] == sites[-1][3] == sites[0][3] > 0
    assert [p[2] for p in sites] == sorted(p[2] for p in sites) and len(sites) == 11
    kinds = [kind if kind == "well_sites" else payload[1] for kind, payload in events]
    assert kinds == [0, 1, *["well_sites"] * 11, 2]


def test_the_resize_choice_reaches_cellpose_and_the_run_s_settings(tmp_path, monkeypatch, rendered_well):
    """Asked for: a run that does not resize for Cellpose. At 0.65 µm/px a 10 µm cell is 15 px, which Cellpose
    enlarges x2 to its 30 px: four times the pixels. ``none`` must tell Cellpose no diameter at all (0, so no
    resize), on the run's own path; the default keeps telling it the cell diameter in pixels."""
    from tests._nanowell_run_fixture import PX

    rendered, record, _truth = rendered_well
    told = []

    def run(name, *extra):
        (tmp_path / name).mkdir()
        out = _run_main(monkeypatch, _write_plate(tmp_path / name, rendered, record), "--nanowell_format", "100um",
                        *extra, segmenter_factory=lambda diameter_px=0.0, **k: told.append(diameter_px)
                        or threshold_segmenter())
        return json.loads((out / "pipeline_info.json").read_text())["nanowell"]["settings"]

    default = run("default")
    assert told and told == [pytest.approx(15.0 / PX)] * len(told) and "resize" not in default
    told.clear()
    none = run("none", "--nanowell_resize", "none")
    assert told and set(told) == {0.0} and none["resize"] == "none"


def test_the_key_the_app_computes_is_the_one_the_pipeline_records(tmp_path, monkeypatch):
    """The whole feature rests on this: the key derived from the argv equals what ``process_microscopy.main``
    writes beside the well, for a nanowell run (whose options rewrite the method and the nuclear slot)."""
    from pipeline.cli_args import settings_key_from_argv
    from pipeline.settings_record import recorded_well_settings

    rendered, record = build_well(timepoint="0h")
    plate = _write_plate(tmp_path, rendered, record)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", "--nanowell_resize", "none")
    argv = sys.argv[1:]
    recorded = recorded_well_settings(out, "A01")
    assert recorded and recorded == settings_key_from_argv(argv).split("~")[0]
    assert json.loads((out / ".aw_settings" / "A01.json").read_text())["digest"] == recorded


def test_a_real_nanowell_well_reads_as_finished_and_a_new_timepoint_unfinishes_it(tmp_path, monkeypatch):
    """The remote finished-wells rule (``services.remote_finished_wells``) on the pipeline's own output rather than
    hand-made files: the CSV, settings record, grid record and completeness record a real run writes read as a
    finished well under the same settings — and stop doing so when the archive gains a timepoint, or the settings
    change."""
    from services.remote_finished_wells import finished_here

    rendered, record = build_well(timepoint="0h")
    plate = _write_plate(tmp_path, rendered, record)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um")
    argv = ["python", "process_microscopy.py", *sys.argv[1:]]
    assert finished_here(out, argv, ["A01"]) == ["A01"]
    assert finished_here(out, [*argv, "--nanowell_resize", "none"], ["A01"]) == []
    with zipfile.ZipFile(plate / "in" / f"{WELL}.zip", "a") as zf:
        zf.writestr(f"exp_PHASE_{WELL}_F0_24h.tif", b"x")
    assert finished_here(out, argv, ["A01"]) == []


def test_a_concatenation_of_two_prefixed_plates_is_detected_and_counted(tmp_path, monkeypatch):
    """Reported: a concatenated nanowell plate, every image correctly named but for each acquisition's own prefix,
    failed with "No image groups found". The schema the app detects from the archive took the prefix for the
    channel. Both timepoints are now detected, grouped and measured, through the real Concatenate and the real run."""
    from datasets.concatenate import apply
    from datasets.concatenate import concatenate_compute as cc
    from util.schema_infer import infer_from_dir

    def plate(root, prefix):
        rendered, record = build_well(timepoint="0h")
        (root / "in").mkdir(parents=True)
        tif = tmp_path / "frame.tif"
        with zipfile.ZipFile(root / "in" / f"{WELL}.zip", "w") as zf:
            for fov, image in rendered.images.items():
                for channel, frame in (("PHASE", image), ("GFP", image * 0 + 10.0)):
                    imwrite(str(tif), frame.astype("float32"))
                    zf.write(tif, f"{prefix}_{channel}_{WELL}_{fov}_0h.tif")
        write_record_file(root / "in" / "acquisition.json", record)

    plate(tmp_path / "d1", "expA")
    plate(tmp_path / "d2", "expB")
    apply.execute_plan(cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=24.0))
    guess = infer_from_dir(tmp_path / "dest" / "in")
    assert guess.confident and guess.fields[1] == "channel" and set(guess.channels) == {"PHASE", "GFP"}
    out = _run_main(monkeypatch, tmp_path / "dest", "--nanowell_format", "100um",
                    "--filename_schema", ":".join(guess.fields))
    grid = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())
    assert {tp: block["status"] for tp, block in grid["timepoints"].items()} == {"0h": "ok", "24h": "ok"}


def test_no_image_groups_says_why(tmp_path):
    """"No image groups found" named no cause. It now says what the channel position holds, or which partner image
    is missing — the two causes a user cannot see from the archive's names alone."""
    from pipeline.nanowell_task import no_groups_reason

    archive = tmp_path / "A01.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for prefix in ("expA", "expB"):
            for channel in ("PHASE", "GFP"):
                zf.writestr(f"{prefix}_{channel}_A01_F0_0h.tif", b"x")
    wrong = no_groups_reason(archive, "PHASE", ["GFP"], ["channel", "ignore", "well", "fov", "timepoint"], "_")
    assert "no image has the phase channel 'PHASE'" in wrong and "'expA' (2), 'expB' (2)" in wrong
    right = no_groups_reason(archive, "PHASE", ["RFP"], ["experiment", "channel", "well", "fov", "timepoint"], "_")
    assert "no RFP image" in right and "'expA_RFP_A01_F0_0h.tif'" in right


def test_no_image_groups_names_a_partner_that_differs_only_by_extension(tmp_path):
    """The grouper looks the partner up by its whole name, extension included, so ``.tif`` beside ``.TIF`` is no
    pair; the reason must name that partner rather than fall through to a bare list of channel values."""
    from pipeline.nanowell_task import no_groups_reason

    archive = tmp_path / "A01.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("exp_PHASE_A01_F0_0h.tif", b"x")
        zf.writestr("exp_GFP_A01_F0_0h.TIF", b"x")
    reason = no_groups_reason(archive, "PHASE", ["GFP"], ["experiment", "channel", "well", "fov", "timepoint"], "_")
    assert "no GFP image" in reason and "'exp_GFP_A01_F0_0h.tif'" in reason


# ── a well's confluence is reused only beside the reference it was read against (review tracker M7) ──────────
REFERENCE = "H12"


def _well_archive(plate, root, well, seed):
    """Write *well*'s single-tile archive into *plate*'s ``in/`` and return its record."""
    rendered, record = build_well(rows=1, cols=1, tile=(900, 900), well=well, timepoint="0h", radius_fraction=0.26,
                                  seed=seed)
    tif = root / "frame.tif"
    with zipfile.ZipFile(plate / "in" / f"{well}.zip", "w") as zf:
        for fov, image in rendered.images.items():
            for channel, frame in (("PHASE", image), ("GFP", image * 0 + 10.0)):
                imwrite(str(tif), frame.astype("float32"))
                zf.write(tif, f"exp_{channel}_{well}_{fov}_0h.tif")
    return record


def _reference_plate(root, seeds: dict):
    """``in/`` with one single-tile archive per well of *seeds* (``well -> render seed``) and their record."""
    plate = root / "plate"
    (plate / "in").mkdir(parents=True, exist_ok=True)
    records = [_well_archive(plate, root, well, seed) for well, seed in seeds.items()]
    record = records[0]
    for other in records[1:]:
        record["tiles"] += other["tiles"]
    write_record_file(plate / "in" / "acquisition.json", record)
    return plate


def _run_plate(monkeypatch, plate, caplog, *extra):
    """``process_microscopy.main`` with the real ``process_well_zips`` — the reference's pool first, then the rest,
    each skipping what is up to date — on an in-process pool, so the stand-in segmenter reaches the wells."""
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(pm, "_well_pool", lambda *a, **k: ThreadPoolExecutor(max_workers=1))
    monkeypatch.setattr(nm, "cellpose_segmenter", lambda *a, **k: threshold_segmenter())
    monkeypatch.setattr(_suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(_suspend, "watch_parent", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", [
        "process_microscopy.py", "--input_dir", str(plate / "in"), "--output_dir", str(plate / "out"),
        "--phase_token", "PHASE", "--fluor_tokens", "GFP", "--filename_schema", ":".join(SCHEMA), "--workers", "1",
        "--background", "none", "--flatfield", "none", "--no_auto_threshold", "--no_run_check",
        "--nanowell_segment_on", "rim", "--nanowell_format", "100um", "--nanowell_empty_well", REFERENCE, *extra])
    caplog.clear()
    with caplog.at_level("INFO"):
        pm.main()
    return plate / "out", [r.getMessage() for r in caplog.records]


def _confluence(out, well):
    return {r["confluence"] for r in _rows(out, f"{well}.csv") if r["state"] in ("measured", "empty")}


@pytest.mark.parametrize("mode", [(), ("--incremental",)], ids=["plain", "incremental"])
def test_a_well_read_before_its_reference_was_measured_is_measured_again_once_it_is(
        tmp_path, monkeypatch, caplog, mode):
    """M7 (a): A01 is analysed while the reference H12 is not on the plate, so it has no confluence and says why, and
    is sealed complete. Once H12 is added, a re-run — plain or incremental — measured H12 and then skipped A01 as up
    to date, blank, beside a reference that now gives it a confluence. The settings key names the reference well, not
    what it holds."""
    plate = _reference_plate(tmp_path, {WELL: 1})
    out, _said = _run_plate(monkeypatch, plate, caplog, *mode)
    assert _confluence(out, WELL) == {""}                                   # blank, never 0
    record = json.loads((plate / "in" / "acquisition.json").read_text())
    record["tiles"] += _well_archive(plate, tmp_path, REFERENCE, 5)["tiles"]
    write_record_file(plate / "in" / "acquisition.json", record)

    out, said = _run_plate(monkeypatch, plate, caplog, *mode)

    assert f"Well {WELL} — done. 1 timepoint(s), 0 without rows." in said
    measured = _confluence(out, WELL)
    assert "" not in measured                                               # every nanowell read, now
    values = sorted(float(v) for v in measured)
    assert values[0] >= 0.0 and values[len(values) // 2] < 0.05             # empty nanowells (measured: 0.0 to 0.23)
    from pipeline import nanowell_reference as nref

    grid = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())
    assert grid["timepoints"]["0h"][nref.KEY_FIELD].startswith("template:")


def test_a_re_exported_reference_measures_its_wells_again_and_an_unchanged_one_does_not(tmp_path, monkeypatch, caplog):
    """M7 (b): the plate is analysed with H12 as the reference; H12 is then run again by itself (``--force_wells``).
    Re-run on the same images it saves the same templates — rewritten files, the same content — and A01 is still up
    to date. Re-exported (other images), A01 was read against a reference that is gone: it is analysed again rather
    than kept beside the new one, which mixed the two references' confluence invisibly."""
    plate = _reference_plate(tmp_path, {WELL: 1, REFERENCE: 5})
    out, said = _run_plate(monkeypatch, plate, caplog)
    assert f"Well {WELL} — done. 1 timepoint(s), 0 without rows." in said

    out, said = _run_plate(monkeypatch, plate, caplog, "--force_wells", REFERENCE)
    assert f"Well {REFERENCE} — done. 1 timepoint(s), 0 without rows." in said
    assert f"SKIP well {WELL} — output already exists. Use --force to reprocess." in said

    _well_archive(plate, tmp_path, REFERENCE, 9)                             # the same tile, imaged again
    out, said = _run_plate(monkeypatch, plate, caplog, "--force_wells", REFERENCE)
    assert f"SKIP well {WELL} — output already exists. Use --force to reprocess." not in said
    assert f"Well {WELL} — done. 1 timepoint(s), 0 without rows." in said
    assert any(m.startswith(f"Well {WELL} — its confluence at 0h was read against another empty reference") for m in said)


def test_a_reference_well_that_is_not_on_the_plate_is_warned_about(tmp_path, monkeypatch, caplog):
    """Review tracker L9: a named reference that is neither among the run's wells nor measured in the output folder
    (here H12, which this plate does not have) gives no well a confluence, and the run said nothing until each
    well's notes did. It warns before any well runs; a reference measured earlier is read, so is no reason to."""
    plate = _reference_plate(tmp_path, {WELL: 1})
    _out, said = _run_plate(monkeypatch, plate, caplog)
    warning = (f"The empty reference well {REFERENCE} is not among this run's wells and has not been measured in this "
               "output folder, so no well will have a confluence.")
    assert [m for m in said if m.startswith(warning)] and said.index(
        next(m for m in said if m.startswith(warning))) < said.index(f"Well {WELL} — done. 1 timepoint(s), 0 without rows.")

    alone = json.loads((plate / "in" / "acquisition.json").read_text())
    record = json.loads((plate / "in" / "acquisition.json").read_text())
    record["tiles"] += _well_archive(plate, tmp_path, REFERENCE, 5)["tiles"]
    write_record_file(plate / "in" / "acquisition.json", record)
    _run_plate(monkeypatch, plate, caplog)                          # the reference measured here
    (plate / "in" / f"{REFERENCE}.zip").unlink()                     # then left out of the next run
    write_record_file(plate / "in" / "acquisition.json", alone)
    _out, said = _run_plate(monkeypatch, plate, caplog, "--force")
    assert not [m for m in said if m.startswith(warning)]
