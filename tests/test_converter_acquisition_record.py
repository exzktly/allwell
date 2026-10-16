"""The OME-TIFF and ND2 converters leave ``in/acquisition.json`` beside the archives (tracker I5).

Every stage coordinate in these fixtures is distinct per position **and** per timepoint, so a record that
read the wrong Image, the wrong frame or the wrong timepoint for a tile names a different number than the
one asserted (CLAUDE.md §7.8 rule 2).
"""

from __future__ import annotations

import json
import zipfile

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from pipeline.ome_explode import explode_ome_tiff  # noqa: E402
from util.acquisition_record import read_record_file, validate_against_archives  # noqa: E402


def _stage(pos: int, t: int) -> tuple[float, float]:
    return 1000.0 * pos + 7.0 * t, -500.0 * pos + 3.0 * t


def _write_multi(path, *, positions=4, size_t=2, size_c=2, unit="µm", physical=0.65):
    """One Image per position; each plane at :func:`_stage` in *unit* (``None``: no unit stated)."""
    with tifffile.TiffWriter(str(path), ome=True) as tw:
        for pos in range(positions):
            arr = np.full((size_t, size_c, 6, 8), pos + 1, np.uint16)
            planes = {"PositionX": [_stage(pos, t)[0] for t in range(size_t) for _c in range(size_c)],
                      "PositionY": [_stage(pos, t)[1] for t in range(size_t) for _c in range(size_c)]}
            if unit is not None:
                planes["PositionXUnit"] = [unit] * (size_t * size_c)
                planes["PositionYUnit"] = [unit] * (size_t * size_c)
            meta = {"axes": "TCYX", "Channel": {"Name": ["PH", "GFP"][:size_c]}, "Plane": planes}
            if physical is not None:
                meta.update(PhysicalSizeX=physical, PhysicalSizeY=physical)
            tw.write(arr, metadata=meta)
    return path


def _explode(src, out, **kw):
    return explode_ome_tiff(src, out, grid_rows=1, grid_cols=2, fovs_per_well=2, experiment="exp", **kw)


def test_an_ome_conversion_records_each_tile_s_stage_position_and_the_calibration(tmp_path):
    out = tmp_path / "in"
    _explode(_write_multi(tmp_path / "plate.ome.tif"), out)
    record = read_record_file(out / "acquisition.json")
    tiles = {(t["well"], t["field"], t["timepoint"]): t["stage_xy_um"] for t in record["tiles"]}
    tokens = sorted(record["timepoints"])
    assert len(tiles) == 2 * 2 * 2
    # Position 3 is A02's second field: well 1 x 2 fields + field 1.
    for (well, fov, pos) in (("A01", 0, 0), ("A01", 1, 1), ("A02", 0, 2), ("A02", 1, 3)):
        for t, token in enumerate(tokens):
            assert tiles[(well, f"f{fov}", token)] == list(_stage(pos, t))
    for meta in record["timepoints"].values():
        assert meta["pixel_size_um"] == {"x": 0.65, "y": 0.65}
        assert meta["tile_size_px"] == {"x": 8, "y": 6}
        assert meta["started_at"] is None and meta["start_reason"]
    assert record["source_files"] == ["plate.ome.tif"]
    assert validate_against_archives(tmp_path, record).ok


def test_a_position_in_an_unstated_unit_is_recorded_as_unknown_not_guessed(tmp_path):
    out = tmp_path / "in"
    _explode(_write_multi(tmp_path / "plate.ome.tif", unit=None), out)
    record = read_record_file(out / "acquisition.json")
    assert all(t["stage_xy_um"] is None for t in record["tiles"])
    assert all(m["pixel_size_um"]["x"] == 0.65 for m in record["timepoints"].values())


def test_a_position_in_millimetres_is_converted(tmp_path):
    out = tmp_path / "in"
    explode_ome_tiff(_write_multi(tmp_path / "plate.ome.tif", unit="mm", positions=2), out, grid_rows=1,
                     grid_cols=2, fovs_per_well=1, experiment="exp")
    record = read_record_file(out / "acquisition.json")
    tile = next(t for t in record["tiles"] if t["well"] == "A02" and t["timepoint"] == sorted(record["timepoints"])[1])
    assert tile["stage_xy_um"] == [pytest.approx(1007000.0), pytest.approx(-497000.0)]


def test_a_folder_holding_another_conversion_s_archive_gets_no_record_and_loses_its_stale_one(tmp_path):
    out = tmp_path / "in"
    out.mkdir()
    (out / "acquisition.json").write_text(json.dumps({"stale": True}))
    with zipfile.ZipFile(out / "H12.zip", "w") as zf:
        zf.writestr("other_PH_H12_f0_0h.tif", b"")
    logged = []
    _explode(_write_multi(tmp_path / "plate.ome.tif"), out, log_fn=logged.append)
    assert not (out / "acquisition.json").exists()
    assert any("H12.zip is not this conversion's output" in line for line in logged)


def test_an_archive_of_a_planned_well_written_under_other_settings_withholds_the_record(tmp_path):
    out = tmp_path / "in"
    out.mkdir()
    with zipfile.ZipFile(out / "A02.zip", "w") as zf:       # A02 is this plate's, but not these members
        zf.writestr("exp_PH_A02_f0_0h.tif", b"")
    logged = []
    _explode(_write_multi(tmp_path / "plate.ome.tif"), out, well_subset=("A01",), log_fn=logged.append)
    assert not (out / "acquisition.json").exists()
    assert any("A02.zip is not this conversion's output" in line for line in logged)


def test_a_subset_conversion_records_the_wells_an_earlier_one_wrote_with_the_same_settings(tmp_path):
    out = tmp_path / "in"
    src = _write_multi(tmp_path / "plate.ome.tif")
    _explode(src, out, well_subset=("A01",))
    assert {t["well"] for t in read_record_file(out / "acquisition.json")["tiles"]} == {"A01"}
    _explode(src, out, well_subset=("A02",))
    record = read_record_file(out / "acquisition.json")
    assert {t["well"] for t in record["tiles"]} == {"A01", "A02"}
    assert validate_against_archives(tmp_path, record).ok


def test_a_packed_timelapse_reads_each_tile_from_its_own_frame(tmp_path):
    """Eight frames, position-inner: frame f is position f % 4 at timepoint f // 4."""
    path = tmp_path / "packed.ome.tif"
    tifffile.imwrite(str(path), np.zeros((8, 1, 6, 8), np.uint16), ome=True, photometric="minisblack",
                     metadata={"axes": "TCYX", "Channel": {"Name": ["PH"]}, "PhysicalSizeX": 0.5,
                               "PhysicalSizeY": 0.5,
                               "Plane": {"PositionX": [_stage(f % 4, f // 4)[0] for f in range(8)],
                                         "PositionY": [_stage(f % 4, f // 4)[1] for f in range(8)],
                                         "PositionXUnit": ["µm"] * 8, "PositionYUnit": ["µm"] * 8}})
    out = tmp_path / "in"
    explode_ome_tiff(path, out, grid_rows=1, grid_cols=2, fovs_per_well=2, experiment="exp",
                     frame_order="pos_in_time")
    record = read_record_file(out / "acquisition.json")
    tokens = sorted(record["timepoints"])
    tiles = {(t["well"], t["field"], t["timepoint"]): t["stage_xy_um"] for t in record["tiles"]}
    for (well, fov, pos) in (("A01", 0, 0), ("A01", 1, 1), ("A02", 0, 2), ("A02", 1, 3)):
        for t, token in enumerate(tokens):
            assert tiles[(well, f"f{fov}", token)] == list(_stage(pos, t))


def test_a_packed_single_image_file_reads_each_tile_from_its_own_frame(tmp_path):
    """Four frames, one per position and one timepoint; frame f is position f."""
    path = tmp_path / "packed.ome.tif"
    data = np.zeros((4, 1, 6, 8), np.uint16)
    tifffile.imwrite(str(path), data, ome=True, photometric="minisblack",
                     metadata={"axes": "TCYX", "Channel": {"Name": ["PH"]}, "PhysicalSizeX": 0.5,
                               "PhysicalSizeY": 0.5,
                               "Plane": {"PositionX": [_stage(f, 0)[0] for f in range(4)],
                                         "PositionY": [_stage(f, 0)[1] for f in range(4)],
                                         "PositionXUnit": ["µm"] * 4, "PositionYUnit": ["µm"] * 4}})
    out = tmp_path / "in"
    explode_ome_tiff(path, out, grid_rows=1, grid_cols=2, fovs_per_well=2, experiment="exp")
    record = read_record_file(out / "acquisition.json")
    tiles = {(t["well"], t["field"]): t["stage_xy_um"] for t in record["tiles"]}
    assert tiles == {("A01", "f0"): list(_stage(0, 0)), ("A01", "f1"): list(_stage(1, 0)),
                     ("A02", "f0"): list(_stage(2, 0)), ("A02", "f1"): list(_stage(3, 0))}


# ── ND2 ──────────────────────────────────────────────────────────────────────
def _nd2(tmp_path, **kw):
    pytest.importorskip("nd2")
    from tests._nd2_fixture import write_nd2

    return write_nd2(tmp_path / "plate.nd2", **kw)


def test_an_nd2_conversion_records_each_position_s_stage_point_and_the_calibration(tmp_path):
    from pipeline.nd2_explode import explode_nd2

    stage = [(10.0 * k + 1.0, -20.0 * k - 2.0) for k in range(4)]
    src = _nd2(tmp_path, n_pos=4, n_t=2, stage_um=stage, calibration_um=0.65, times_ms=[0, 3_600_000],
               loop_order="TP")
    out = tmp_path / "in"
    explode_nd2(src, out, well_source="grid", grid_rows=1, grid_cols=2, fovs_per_well=2, experiment="exp")
    record = read_record_file(out / "acquisition.json")
    tiles = {(t["well"], t["field"], t["timepoint"]): t["stage_xy_um"] for t in record["tiles"]}
    assert len(tiles) == 8
    for (well, fov, pos) in (("A01", 0, 0), ("A01", 1, 1), ("A02", 0, 2), ("A02", 1, 3)):
        for token in record["timepoints"]:
            assert tiles[(well, f"f{fov}", token)] == list(stage[pos])
    for meta in record["timepoints"].values():
        assert meta["pixel_size_um"] == {"x": 0.65, "y": 0.65}
        assert meta["tile_size_px"] == {"x": 8, "y": 8}
    assert validate_against_archives(tmp_path, record).ok


def test_an_uncalibrated_nd2_records_no_pixel_size_rather_than_its_placeholder_of_one(tmp_path):
    from pipeline.nd2_explode import explode_nd2

    src = _nd2(tmp_path, n_pos=2, n_t=1)
    out = tmp_path / "in"
    explode_nd2(src, out, well_source="grid", grid_rows=1, grid_cols=2, fovs_per_well=1, experiment="exp")
    record = read_record_file(out / "acquisition.json")
    assert all(m["pixel_size_um"] == {"x": None, "y": None} for m in record["timepoints"].values())
    assert {t["well"]: t["stage_xy_um"] for t in record["tiles"]} == {"A01": [0.0, 0.0], "A02": [100.0, 0.0]}


# ── the app's sequence: convert, then run nanowell mode on what was converted ──
def _convert_rendered_well(tmp_path, rendered):
    """The rendered well's tiles as an OME-TIFF (one Image per tile, row by row, positions in µm), exploded into
    ``plate/in`` as the OME converter would — the record included."""
    from tests._nanowell_run_fixture import PX

    src = tmp_path / "well.ome.tif"
    names = sorted(rendered.images)
    with tifffile.TiffWriter(str(src), ome=True) as tw:
        for name in names:
            image = rendered.images[name].astype("float32")
            left, top = rendered.recorded_left_top_um[name]
            tw.write(np.stack([image, image * 0 + 10.0])[None], metadata={
                "axes": "TCYX", "Channel": {"Name": ["PHASE", "GFP"]}, "PhysicalSizeX": PX, "PhysicalSizeY": PX,
                "Plane": {"PositionX": [left] * 2, "PositionY": [top] * 2, "PositionXUnit": ["µm"] * 2,
                          "PositionYUnit": ["µm"] * 2}})
    plate = tmp_path / "plate"
    explode_ome_tiff(src, plate / "in", grid_rows=1, grid_cols=1, fovs_per_well=len(names), experiment="exp")
    return plate


def test_a_converted_ome_well_runs_from_the_described_grid_its_stage_only_record_cannot_place(
        tmp_path, monkeypatch, rendered_well):
    from tests.test_nanowell_cli import _GRID, WELL, _run_main

    rendered, _record, truth = rendered_well
    plate = _convert_rendered_well(tmp_path, rendered)
    record = read_record_file(plate / "in" / "acquisition.json")
    assert all(t["stage_xy_um"] is not None and t["mosaic_xy_px"] is None for t in record["tiles"])
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um", *_GRID)
    grid = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())
    (block,) = grid["timepoints"].values()
    assert block["status"] == "ok"
    sites = {(int(r["nw_q"]), int(r["nw_r"])) for r in
             __import__("csv").DictReader((out / "nanowells" / f"{WELL}.csv").open()) if r["nw_q"]}
    assert set(truth) <= sites


def test_a_converted_ome_well_without_a_described_grid_is_refused_naming_it(tmp_path, monkeypatch, rendered_well):
    from tests.test_nanowell_cli import WELL, _run_main

    rendered, _record, _truth = rendered_well
    plate = _convert_rendered_well(tmp_path, rendered)
    with pytest.raises(Exception, match="a stage position alone cannot be placed.*--nanowell_grid"):
        _run_main(monkeypatch, plate, "--nanowell_format", "100um")
    assert not (plate / "out" / "nanowells" / f"{WELL}_grid.json").exists()


@pytest.fixture(scope="module")
def rendered_well():
    from tests.test_nanowell_cli import rendered_well as fixture

    return fixture.__wrapped__()


def test_the_analyze_pre_flight_asks_for_the_grid_a_stage_only_record_cannot_replace(tmp_path, rendered_well):
    from analyze.nanowell_preflight import nanowell_input_report

    rendered, _record, _truth = rendered_well
    plate = _convert_rendered_well(tmp_path, rendered)
    opts = {"filename_schema": "experiment:channel:well:fov:timepoint", "filename_sep": "_", "phase_token": "PHASE",
            "nanowell_format": "100um"}
    problems, _ = nanowell_input_report(str(plate), opts)
    assert problems == ["The acquisition record does not say where the tiles sit in the image frame (no mosaic "
                        "position, or no pixel size), so describe the tile grid instead (Tile grid, Scan order, "
                        "Tile overlap, Pixel size): A01"]
    problems, warnings = nanowell_input_report(str(plate), {**opts, "nanowell_grid": "2x2", "nanowell_scan":
                                                            "top-left-row", "nanowell_overlap_percent": "10",
                                                            "nanowell_pixel_size_um": "4.3"})
    assert problems == []
    assert warnings[0].startswith("The acquisition record cannot place 1 well(s) (no mosaic position, or no pixel "
                                  "size): the tiles will be placed from the 2 x 2 grid")
    assert not any("There is no acquisition record" in w for w in warnings)


def test_an_nd2_conversion_into_a_folder_it_cannot_record_drops_the_stale_record(tmp_path):
    from pipeline.nd2_explode import explode_nd2

    out = tmp_path / "in"
    out.mkdir()
    (out / "acquisition.json").write_text(json.dumps({"stale": True}))
    with zipfile.ZipFile(out / "H12.zip", "w") as zf:
        zf.writestr("other_PH_H12_f0_0h.tif", b"")
    explode_nd2(_nd2(tmp_path, n_pos=2, n_t=1), out, well_source="grid", grid_rows=1, grid_cols=2,
                fovs_per_well=1, experiment="exp")
    assert not (out / "acquisition.json").exists()


def test_a_converted_folder_changed_by_hand_is_refused_with_the_way_out(tmp_path):
    """Every run checks a record against its archives; one written by the converter is no exception, so a well
    deleted by hand afterwards stops the run — and the refusal names what to do."""
    from pipeline.sidecar import check_acquisition_inputs
    from util.acquisition_record import AcquisitionRecordError

    out = tmp_path / "in"
    _explode(_write_multi(tmp_path / "plate.ome.tif"), out)
    check_acquisition_inputs(out, "experiment:channel:well:fov:timepoint", "_")
    (out / "A02.zip").unlink()
    with pytest.raises(AcquisitionRecordError, match=r"Record names absent well archive A02.*no longer matches the "
                                                     r"well archives in in/.*Subset.*delete acquisition\.json"):
        check_acquisition_inputs(out, "experiment:channel:well:fov:timepoint", "_")


def test_an_archive_another_file_converted_under_the_same_settings_withholds_the_record(tmp_path):
    """The same grid and experiment over two different files write identical member names; the second
    conversion must not record the first file's well with its own stage positions."""
    out = tmp_path / "in"
    _explode(_write_multi(tmp_path / "first.ome.tif"), out, well_subset=("A01",))
    assert read_record_file(out / "acquisition.json")["source_files"] == ["first.ome.tif"]
    logged = []
    _explode(_write_multi(tmp_path / "second.ome.tif"), out, well_subset=("A02",), log_fn=logged.append)
    assert not (out / "acquisition.json").exists()
    assert any("A01.zip is not this conversion's output" in line for line in logged)


def test_ome_metadata_that_cannot_be_read_costs_the_record_not_the_conversion(tmp_path):
    """Roll-up review X27: opening the OME file for its metadata sat outside the record's never-raises contract, so
    an unreadable one failed a conversion whose images were already written. It is said in the run log instead."""
    from pipeline.ome_explode import _publish_record

    said = []
    _publish_record(tmp_path / "gone.ome.tif", tmp_path / "in", None, ["A01"], "exp", 1, [(0, "0h")],
                    (1, 1, "TZ"), said.append)
    assert len(said) == 1 and said[0].startswith(
        "[record] No acquisition record written: the OME metadata could not be read (FileNotFoundError")
    assert not (tmp_path / "in" / "acquisition.json").exists()
