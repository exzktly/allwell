"""Qt-free planning for the OME-TIFF converter (``datasets.ome_import``)."""

from __future__ import annotations

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from datasets.ome_import.apply import convert  # noqa: E402
from datasets.ome_import.ome_import_compute import (  # noqa: E402
    ConversionParams,
    OmeImportError,
    default_dest,
    plan_conversion,
    read_layout,
    strip_ome_suffix,
)


def _write_multi(path, *, positions, size_t=1, size_c=1, channel_names=("GFP",)):
    with tifffile.TiffWriter(str(path), ome=True) as tw:
        for _ in range(positions):
            arr = np.zeros((size_t, size_c, 6, 6), np.uint16)
            tw.write(arr, metadata={"axes": "TCYX", "Channel": {"Name": list(channel_names)}})
    return path


def _write_packed(path, *, n_frames, size_c=1, channel_names=("GFP",)):
    data = np.zeros((n_frames, size_c, 6, 6), np.uint16)
    for f in range(n_frames):
        for c in range(size_c):
            data[f, c] = f * 10 + c
    tifffile.imwrite(
        str(path), data, ome=True, photometric="minisblack",
        metadata={"axes": "TCYX", "Channel": {"Name": list(channel_names)}},
    )
    return path


def test_default_dest_and_strip_suffix(tmp_path):
    assert strip_ome_suffix("plate.ome.tif") == "plate"
    assert strip_ome_suffix("plate.ome.tiff") == "plate"
    assert default_dest(tmp_path / "plate.ome.tif") == tmp_path / "plate_dataset"


def test_plan_multi_image_derives_fovs(tmp_path):
    p = _write_multi(tmp_path / "m.ome.tif", positions=6, size_t=2)
    plan = plan_conversion(read_layout(p), ConversionParams(grid_rows=1, grid_cols=3))
    assert not plan.single_image
    assert plan.fovs_per_well == 2
    assert plan.n_timepoints == 2
    assert plan.well_labels == ["A01", "A02", "A03"]


def test_plan_multi_image_fovs_mismatch(tmp_path):
    p = _write_multi(tmp_path / "m.ome.tif", positions=6)
    with pytest.raises(OmeImportError):
        plan_conversion(read_layout(p),
                        ConversionParams(grid_rows=2, grid_cols=2, fovs_per_well=2))


def test_plan_single_image_frame_orders(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=12)
    layout = read_layout(p)
    # single timepoint needs wells×fovs == frames.
    with pytest.raises(OmeImportError):
        plan_conversion(layout, ConversionParams(grid_rows=1, grid_cols=3,
                                                 fovs_per_well=2, frame_order="single"))
    # positions-within-timepoint: 6 positions × 2 timepoints.
    plan = plan_conversion(layout, ConversionParams(
        grid_rows=1, grid_cols=3, fovs_per_well=2, frame_order="pos_in_time"))
    assert plan.single_image
    assert plan.n_positions == 6
    assert plan.n_timepoints == 2


def test_plan_single_image_needs_fovs_with_timepoints(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=12)
    with pytest.raises(OmeImportError, match="FOVs per well"):
        plan_conversion(read_layout(p),
                        ConversionParams(grid_rows=1, grid_cols=3, frame_order="pos_in_time"))


def test_plan_grid_overflow_rejected(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=4)
    with pytest.raises(OmeImportError):
        plan_conversion(read_layout(p), ConversionParams(
            grid_rows=2, grid_cols=2, origin="H12", fovs_per_well=1))


def test_convert_writes_dataset_folder(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=6, size_c=2,
                      channel_names=("DAPI", "GFP"))
    dest = tmp_path / "out_ds"
    params = ConversionParams(grid_rows=1, grid_cols=3, fovs_per_well=2,
                              frame_order="pos_in_time")
    summary = convert(p, dest, params)
    # Output is per-well zips (ready Analyze input), not loose TIFs.
    import zipfile
    zips = sorted(z.name for z in (dest / "in").glob("*.zip"))
    assert zips == ["A01.zip", "A02.zip", "A03.zip"]
    assert "3 well(s)" in summary
    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:
        members = zf.namelist()
    # 2 FOVs × 2 channels × 1 timepoint per well; experiment token = file stem.
    assert len(members) == 4
    assert "p_DAPI_A01_f0_t0.tif" in members


def test_convert_output_is_ready_analyze_input(tmp_path):
    # The per-well zips must be usable by resolve_input_output *directly* — no
    # WellPlateZipper regrouping pass (that's the point of zip-direct output).
    from services.input_resolution_service import resolve_input_output

    p = _write_packed(tmp_path / "p.ome.tif", n_frames=6)
    dest = tmp_path / "ds"
    convert(p, dest, ConversionParams(grid_rows=1, grid_cols=3, fovs_per_well=2,
                                      frame_order="pos_in_time"))
    logs: list[str] = []
    in_dir, out_dir, on_success = resolve_input_output(
        dest, log_fn=logs.append,
        filename_schema="experiment:channel:well:fov:timepoint", filename_sep="_")
    assert in_dir == dest / "in"
    assert out_dir == dest / "out"
    assert on_success is None  # nothing to clean up — already grouped
    assert not any("zipper" in m.lower() for m in logs)
