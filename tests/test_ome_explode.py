"""De-interleaving a packed OME-TIFF into per-well ``.zip`` archives (``pipeline.ome_explode``)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from pipeline.ome_explode import (  # noqa: E402
    OmeExplodeCancelled,
    explode_ome_tiff,
    read_ome_layout,
)
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


def _write_packed(path, *, n_frames, size_c=2, channel_names=("DAPI", "GFP")):
    """Write a single-<Image> OME-TIFF that packs *n_frames* into the T axis.

    Pixel value at (frame, c) is ``frame * 10 + c`` — a checkable fingerprint for
    the (position, timepoint) split.
    """
    y = x = 6
    data = np.zeros((n_frames, size_c, y, x), np.uint16)
    for f in range(n_frames):
        for c in range(size_c):
            data[f, c] = f * 10 + c
    tifffile.imwrite(
        str(path), data, ome=True, photometric="minisblack",
        metadata={"axes": "TCYX", "Channel": {"Name": list(channel_names)}},
    )
    return path


def _members(dest) -> set[str]:
    """Every member name across all ``<well>.zip`` archives in *dest*."""
    out: set[str] = set()
    for z in Path(dest).glob("*.zip"):
        with zipfile.ZipFile(z) as zf:
            out.update(zf.namelist())
    return out


def _read(dest, well: str, member: str):
    """Read one member of a well's archive as a 2-D array."""
    with zipfile.ZipFile(Path(dest) / f"{well}.zip") as zf:
        return tifffile.imread(io.BytesIO(zf.read(member)))


def _zips(dest) -> list[str]:
    return sorted(p.name for p in Path(dest).glob("*.zip")) if Path(dest).exists() else []


def test_read_layout_reports_sizes_and_names(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=4, size_t=2, size_c=2, size_z=3)
    layout = read_ome_layout(p)
    assert layout.n_positions == 4
    assert layout.size_t == 2
    assert layout.size_c == 2
    assert layout.size_z == 3
    assert layout.channel_names == ["DAPI", "GFP"]
    assert layout.timepoint_tokens == ["t0", "t1"]


def test_explode_writes_per_well_zips(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=4, size_t=2, size_c=2, size_z=3)
    out = tmp_path / "in"
    res = explode_ome_tiff(
        p, out, grid_rows=2, grid_cols=2, origin_well="A01",
        direction="row", experiment="exp",
    )
    # 4 wells × 1 FOV × 2 channels × 2 timepoints = 16 planes, one zip per well.
    assert res.images_written == 16
    assert res.fovs_per_well == 1
    assert res.wells == ["A01", "A02", "B01", "B02"]
    assert _zips(out) == ["A01.zip", "A02.zip", "B01.zip", "B02.zip"]
    assert _members(out) == {
        f"exp_{ch}_{well}_f0_{tp}.tif"
        for well in ("A01", "A02", "B01", "B02")
        for ch in ("DAPI", "GFP")
        for tp in ("t0", "t1")
    }


def test_explode_max_projects_z(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=4, size_t=1, size_c=2, size_z=3)
    out = tmp_path / "in"
    explode_ome_tiff(
        p, out, grid_rows=2, grid_cols=2, origin_well="A01",
        direction="row", experiment="exp",
    )
    # Position 0 → well A01. base(0, 0, 0) = 0, so max over Z=3 planes is 0+2 = 2.
    assert int(_read(out, "A01", "exp_DAPI_A01_f0_t0.tif").max()) == 2


def test_exploded_member_names_round_trip_through_parse_filename(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=2, size_t=1, size_c=2, size_z=1)
    out = tmp_path / "in"
    explode_ome_tiff(
        p, out, grid_rows=1, grid_cols=2, origin_well="A01",
        direction="row", experiment="exp",
    )
    schema = parse_schema("experiment:channel:well:fov:timepoint")
    meta = parse_filename("exp_GFP_A02_f0_t0.tif", schema=schema, sep=DEFAULT_SEP)
    assert meta["experiment"] == "exp"
    assert meta["channel"] == "GFP"
    assert meta["well"] == "A02"
    assert meta["fov"] == "f0"
    assert "exp_GFP_A02_f0_t0.tif" in _members(out)


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
    assert "exp_c0_A02_f1_t0.tif" in _members(out)


def test_explicit_fovs_per_well_matches_derived(tmp_path):
    p = _write_ome(
        tmp_path / "s.ome.tif", positions=6, size_t=1, size_c=1, size_z=1,
        channel_names=("",),
    )
    auto = tmp_path / "auto"
    explicit = tmp_path / "explicit"
    r_auto = explode_ome_tiff(p, auto, grid_rows=1, grid_cols=3, experiment="exp")
    r_exp = explode_ome_tiff(
        p, explicit, grid_rows=1, grid_cols=3, fovs_per_well=2, experiment="exp")
    assert r_exp.fovs_per_well == r_auto.fovs_per_well == 2
    assert _members(explicit) == _members(auto)


def test_explicit_well_order_non_rectangular(tmp_path):
    # 3 positions, an explicit non-rectangular / non-serpentine ordering. The
    # per-well zips are exactly those wells, and the position→well mapping
    # follows the given order (position i → well_order[i], pixel-identity).
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=3, size_c=1,
                      channel_names=("GFP",))
    out = tmp_path / "in"
    res = explode_ome_tiff(
        p, out, grid_rows=1, grid_cols=1, fovs_per_well=1,
        frame_order="single", well_order=["A01", "H12", "B02"], experiment="e",
    )
    assert res.wells == ["A01", "B02", "H12"]      # sorted set of the given wells
    assert _zips(out) == ["A01.zip", "B02.zip", "H12.zip"]
    # Frame value is frame*10. Position 1 → well H12 (well_order[1]); pixel = 10.
    assert int(_read(out, "H12", "e_GFP_H12_f0_t0.tif").max()) == 10
    # Position 2 → well B02; pixel = 20.
    assert int(_read(out, "B02", "e_GFP_B02_f0_t0.tif").max()) == 20


def test_well_order_duplicate_rejected_before_writing(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=2, size_c=1,
                      channel_names=("GFP",))
    out = tmp_path / "in"
    with pytest.raises(ValueError, match="more than once"):
        explode_ome_tiff(
            p, out, grid_rows=1, grid_cols=1, fovs_per_well=1,
            frame_order="single", well_order=["A01", "A01"], experiment="e",
        )
    assert _zips(out) == []


def test_explicit_fovs_per_well_mismatch_rejected(tmp_path):
    # 6 positions, but 2×2 wells × 2 FOVs = 8 ≠ 6.
    p = _write_ome(tmp_path / "s.ome.tif", positions=6, size_t=1, size_c=1, size_z=1)
    out = tmp_path / "in"
    with pytest.raises(ValueError, match="stage position"):
        explode_ome_tiff(
            p, out, grid_rows=2, grid_cols=2, fovs_per_well=2, experiment="exp")
    assert _zips(out) == []


def test_single_image_layout_reports_frames(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=12, size_c=3,
                      channel_names=("BF", "DAPI", "GFP"))
    layout = read_ome_layout(p)
    assert layout.single_image is True
    assert layout.n_frames == 12
    assert layout.size_c == 3
    assert layout.channel_names == ["BF", "DAPI", "GFP"]


def test_single_image_single_timepoint_maps_frames_to_positions(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=6, size_c=1,
                      channel_names=("GFP",))
    out = tmp_path / "in"
    res = explode_ome_tiff(
        p, out, grid_rows=1, grid_cols=3, fovs_per_well=2,
        frame_order="single", experiment="e",
    )
    assert res.wells == ["A01", "A02", "A03"]
    assert res.timepoints == ["t0"]
    assert res.images_written == 6
    # Frame 3 → position 3 → well A02 (3 // 2), FOV 1; pixel = 3*10+0 = 30.
    assert int(_read(out, "A02", "e_GFP_A02_f1_t0.tif").max()) == 30


def test_single_image_positions_within_timepoint(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=12, size_c=1,
                      channel_names=("GFP",))
    out = tmp_path / "in"
    res = explode_ome_tiff(
        p, out, grid_rows=1, grid_cols=3, fovs_per_well=2,
        frame_order="pos_in_time", experiment="e",
    )
    assert res.timepoints == ["t0", "t1"]
    assert res.images_written == 12
    # frame = t*6 + p. Frame 7 → t1, position 1 → A01 FOV 1; pixel = 70.
    assert int(_read(out, "A01", "e_GFP_A01_f1_t1.tif").max()) == 70
    # Frame 4 → t0, position 4 → A03 FOV 0; pixel = 40.
    assert int(_read(out, "A03", "e_GFP_A03_f0_t0.tif").max()) == 40


def test_single_image_timepoints_within_position(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=12, size_c=1,
                      channel_names=("GFP",))
    out = tmp_path / "in"
    explode_ome_tiff(
        p, out, grid_rows=1, grid_cols=3, fovs_per_well=2,
        frame_order="time_in_pos", experiment="e",
    )
    # frame = p*2 + t. Frame 7 → position 3 (A02 FOV 1), t1; pixel = 70.
    assert int(_read(out, "A02", "e_GFP_A02_f1_t1.tif").max()) == 70


def test_parallel_output_matches_serial(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=12, size_c=2)
    serial, par = tmp_path / "serial", tmp_path / "par"
    explode_ome_tiff(p, serial, grid_rows=1, grid_cols=3, fovs_per_well=2,
                     frame_order="pos_in_time", experiment="e", workers=1)
    explode_ome_tiff(p, par, grid_rows=1, grid_cols=3, fovs_per_well=2,
                     frame_order="pos_in_time", experiment="e", workers=4)

    def digest(dest):
        out = {}
        for z in sorted(Path(dest).glob("*.zip")):
            with zipfile.ZipFile(z) as zf:
                for name in zf.namelist():
                    out[(z.name, name)] = zf.read(name)
        return out

    assert digest(serial) == digest(par)


def test_single_image_frame_split_mismatch_rejected(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=10, size_c=1,
                      channel_names=("GFP",))
    out = tmp_path / "in"
    with pytest.raises(ValueError, match="split|divide"):
        explode_ome_tiff(
            p, out, grid_rows=1, grid_cols=3, fovs_per_well=2,
            frame_order="pos_in_time", experiment="e",
        )


def test_single_image_timepoints_need_fovs(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=12, size_c=1,
                      channel_names=("GFP",))
    with pytest.raises(ValueError, match="FOVs per well"):
        explode_ome_tiff(
            p, tmp_path / "in", grid_rows=1, grid_cols=3,
            frame_order="pos_in_time", experiment="e",
        )


def test_unknown_frame_order_rejected(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=6, size_c=1,
                      channel_names=("GFP",))
    with pytest.raises(ValueError, match="frame order"):
        explode_ome_tiff(
            p, tmp_path / "in", grid_rows=1, grid_cols=3, fovs_per_well=2,
            frame_order="sideways", experiment="e",
        )


def test_progress_reports_every_plane(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=4, size_t=1, size_c=2, size_z=1)
    seen: list[tuple[int, int]] = []
    explode_ome_tiff(
        p, tmp_path / "in", grid_rows=2, grid_cols=2, experiment="e", workers=1,
        progress_fn=lambda done, total: seen.append((done, total)),
    )
    # 4 wells × 2 channels = 8 planes; serial → (0,8) then 1..8.
    assert seen[0] == (0, 8)
    assert seen[-1] == (8, 8)
    assert len(seen) == 9


def test_should_abort_cancels_and_leaves_no_zip(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=4, size_t=1, size_c=1)
    out = tmp_path / "in"
    with pytest.raises(OmeExplodeCancelled):
        explode_ome_tiff(
            p, out, grid_rows=2, grid_cols=2, experiment="e", workers=1,
            should_abort=lambda: True,  # abort before the first well
        )
    assert _zips(out) == []


def test_indivisible_position_count_rejected(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=5, size_t=1, size_c=1, size_z=1)
    with pytest.raises(ValueError, match="not divisible"):
        explode_ome_tiff(
            p, tmp_path / "in", grid_rows=2, grid_cols=2, experiment="exp")


def test_grid_overflowing_plate_rejected_before_writing(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=4, size_t=1, size_c=1, size_z=1)
    out = tmp_path / "in"
    with pytest.raises(ValueError):
        explode_ome_tiff(
            p, out, grid_rows=2, grid_cols=2, origin_well="H12", experiment="exp")
    assert _zips(out) == []


def test_missing_channel_names_fall_back_to_positional_tokens(tmp_path):
    p = _write_ome(
        tmp_path / "s.ome.tif", positions=2, size_c=2, channel_names=("", ""),
    )
    layout = read_ome_layout(p)
    assert layout.channel_names == ["c0", "c1"]


def test_per_well_progress_splits_the_run_by_well(tmp_path):
    """The run-wide plane counter cannot say which well is being written, so a
    page with a plate above its progress bar had nothing to paint onto it. Each
    well's own share is reported alongside the total: four planes per well here
    (1 FOV x 2 channels x 2 timepoints), counted from 1 to 4 for each."""
    p = _write_ome(tmp_path / "s.ome.tif", positions=4, size_t=2, size_c=2, size_z=1)
    seen: list[tuple[str, int, int]] = []
    explode_ome_tiff(
        p, tmp_path / "in", grid_rows=2, grid_cols=2, origin_well="A01",
        direction="row", experiment="exp", workers=1,
        well_progress_fn=lambda w, d, t: seen.append((w, d, t)),
    )
    assert {w for w, _d, _t in seen} == {"A01", "A02", "B01", "B02"}
    assert {t for _w, _d, t in seen} == {4}          # planes per well
    for well in ("A01", "A02", "B01", "B02"):
        ticks = [d for w, d, _t in seen if w == well]
        assert ticks == [1, 2, 3, 4], (well, ticks)


def test_per_well_progress_is_optional(tmp_path):
    """Every existing caller passes no well_progress_fn and must be unaffected."""
    p = _write_ome(tmp_path / "s.ome.tif", positions=2, size_t=1, size_c=1, size_z=1)
    res = explode_ome_tiff(
        p, tmp_path / "in", grid_rows=1, grid_cols=2, origin_well="A01",
        direction="row", experiment="exp",
    )
    assert res.images_written == 2
