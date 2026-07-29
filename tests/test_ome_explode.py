"""De-interleaving a packed OME-TIFF into loose per-plane TIFs (``pipeline.ome_explode``)."""

from __future__ import annotations

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from pipeline.ome_explode import explode_ome_tiff, read_ome_layout  # noqa: E402
from process_microscopy import parse_filename  # noqa: E402
from util.schema import DEFAULT_SEP, parse_schema  # noqa: E402


def _write_ome(
    path,
    *,
    positions,
    size_t=1,
    size_c=2,
    size_z=1,
    channel_names=("DAPI", "GFP"),
    base=None,
):
    """Write a synthetic multi-position OME-TIFF (one Image per position).

    Pixel value at (pos, t, c, z) is ``base(pos, t, c) + z`` so a Z max-projection
    is exactly ``base(pos, t, c) + (size_z - 1)`` — a checkable fingerprint.
    """
    if base is None:
        def base(pos, t, c):
            return pos * 100 + t * 10 + c
    y = x = 6
    with tifffile.TiffWriter(str(path), ome=True) as tw:
        for pos in range(positions):
            arr = np.zeros((size_t, size_c, size_z, y, x), np.uint16)
            for t in range(size_t):
                for c in range(size_c):
                    for z in range(size_z):
                        arr[t, c, z] = base(pos, t, c) + z
            tw.write(
                arr,
                metadata={"axes": "TCZYX", "Channel": {"Name": list(channel_names)}},
            )
    return path


def test_read_layout_reports_sizes_and_names(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=4, size_t=2, size_c=2, size_z=3)
    layout = read_ome_layout(p)
    assert layout.n_positions == 4
    assert layout.size_t == 2
    assert layout.size_c == 2
    assert layout.size_z == 3
    assert layout.channel_names == ["DAPI", "GFP"]
    assert layout.timepoint_tokens == ["t0", "t1"]


def test_explode_writes_one_tif_per_plane(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=4, size_t=2, size_c=2, size_z=3)
    out = tmp_path / "in"
    res = explode_ome_tiff(
        p, out, grid_rows=2, grid_cols=2, origin_well="A01",
        direction="row", experiment="exp",
    )
    # 4 wells × 1 FOV × 2 channels × 2 timepoints = 16 planes.
    assert res.images_written == 16
    assert res.fovs_per_well == 1
    assert res.wells == ["A01", "A02", "B01", "B02"]
    assert sorted(x.name for x in out.glob("*.tif")) == sorted(
        f"exp_{ch}_{well}_f0_{tp}.tif"
        for well in ("A01", "A02", "B01", "B02")
        for ch in ("DAPI", "GFP")
        for tp in ("t0", "t1")
    )


def test_explode_max_projects_z(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=4, size_t=1, size_c=2, size_z=3)
    out = tmp_path / "in"
    explode_ome_tiff(
        p, out, grid_rows=2, grid_cols=2, origin_well="A01",
        direction="row", experiment="exp",
    )
    # Position 0 → well A01. base(0, 0, 0) = 0, so max over Z=3 planes is 0+2 = 2.
    img = tifffile.imread(out / "exp_DAPI_A01_f0_t0.tif")
    assert int(img.max()) == 2


def test_exploded_names_round_trip_through_parse_filename(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=2, size_t=1, size_c=2, size_z=1)
    out = tmp_path / "in"
    explode_ome_tiff(
        p, out, grid_rows=1, grid_cols=2, origin_well="A01",
        direction="row", experiment="exp",
    )
    schema = parse_schema("experiment:channel:well:fov:timepoint")
    meta = parse_filename(out / "exp_GFP_A02_f0_t0.tif", schema=schema, sep=DEFAULT_SEP)
    assert meta["experiment"] == "exp"
    assert meta["channel"] == "GFP"
    assert meta["well"] == "A02"
    assert meta["fov"] == "f0"


def test_multiple_fovs_per_well_derived_from_position_count(tmp_path):
    # 6 positions over a 1×3 well grid → 2 FOVs per well.
    p = _write_ome(
        tmp_path / "s.ome.tif", positions=6, size_t=1, size_c=1, size_z=1,
        channel_names=("",),  # unnamed → positional token c0
    )
    out = tmp_path / "in"
    res = explode_ome_tiff(
        p, out, grid_rows=1, grid_cols=3, origin_well="A01",
        direction="row", experiment="exp",
    )
    assert res.fovs_per_well == 2
    assert res.wells == ["A01", "A02", "A03"]
    # Position 3 → well A02 (index 3 // 2 = 1), FOV 1.
    assert (out / "exp_c0_A02_f1_t0.tif").exists()


def test_indivisible_position_count_rejected(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=5, size_t=1, size_c=1, size_z=1)
    with pytest.raises(ValueError, match="not divisible"):
        explode_ome_tiff(
            p, tmp_path / "in", grid_rows=2, grid_cols=2, experiment="exp",
        )


def test_grid_overflowing_plate_rejected_before_writing(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=4, size_t=1, size_c=1, size_z=1)
    out = tmp_path / "in"
    with pytest.raises(ValueError):
        explode_ome_tiff(
            p, out, grid_rows=2, grid_cols=2, origin_well="H12", experiment="exp",
        )
    assert not out.exists() or not list(out.glob("*.tif"))


def test_missing_channel_names_fall_back_to_positional_tokens(tmp_path):
    p = _write_ome(
        tmp_path / "s.ome.tif", positions=2, size_c=2, channel_names=("", ""),
    )
    layout = read_ome_layout(p)
    assert layout.channel_names == ["c0", "c1"]
