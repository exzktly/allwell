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
    OmeLayout,
    _missing_companion_files,
    _packed_plane,
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


def test_per_well_progress_across_several_workers(tmp_path):
    """The production path runs wells in parallel across a thread pool, so the
    per-well counters must be independent — one worker's ticks must never be
    counted into another's well. ``well_written`` lives inside the per-well
    loop for that reason."""
    p = _write_ome(tmp_path / "s.ome.tif", positions=8, size_t=1, size_c=2, size_z=1)
    seen: list[tuple[str, int, int]] = []
    lock = __import__("threading").Lock()

    def _record(w, d, t):
        with lock:
            seen.append((w, d, t))

    explode_ome_tiff(
        p, tmp_path / "in", grid_rows=2, grid_cols=4, origin_well="A01",
        direction="row", experiment="exp", workers=4,
        well_progress_fn=_record,
    )
    per_well: dict[str, list[int]] = {}
    for well, done, total in seen:
        assert total == 2                       # 1 FOV x 2 channels x 1 timepoint
        per_well.setdefault(well, []).append(done)
    assert len(per_well) == 8
    for well, ticks in per_well.items():
        assert ticks == [1, 2], (well, ticks)


def test_per_well_progress_counts_fovs_and_timepoints_into_the_denominator(tmp_path):
    """A well's share is its FOVs x timepoints x channels, not its positions:
    with 2 FOVs per well the well is only done after both are written."""
    p = _write_ome(tmp_path / "s.ome.tif", positions=4, size_t=3, size_c=2, size_z=1)
    seen: list[tuple[str, int, int]] = []
    explode_ome_tiff(
        p, tmp_path / "in", grid_rows=1, grid_cols=2, origin_well="A01",
        direction="row", experiment="exp", workers=1, fovs_per_well=2,
        well_progress_fn=lambda w, d, t: seen.append((w, d, t)),
    )
    assert {t for _w, _d, t in seen} == {12}    # 2 FOV x 3 tp x 2 ch
    assert [d for w, d, _t in seen if w == "A01"] == list(range(1, 13))


def test_convert_quantises_per_well_ticks_before_the_ui_sees_them(tmp_path):
    """``explode_ome_tiff`` ticks once per plane; ``apply.convert`` folds those
    into whole percentage steps, because every tick that survives becomes a
    queued Qt signal and a plate repaint. A well of thousands of planes must
    not drive thousands of repaints."""
    from datasets.ome_import.apply import convert
    from datasets.ome_import.ome_import_compute import ConversionParams

    # 240 planes per well: 1 FOV x 120 timepoints x 2 channels, so the raw tick
    # count per well is comfortably above the 100-step ceiling.
    p = _write_ome(tmp_path / "s.ome.tif", positions=2, size_t=120, size_c=2, size_z=1)
    seen: list[tuple[str, int, int]] = []
    convert(
        p, tmp_path / "out",
        ConversionParams(grid_rows=1, grid_cols=2, experiment="exp", workers=1),
        well_progress=lambda w, d, t: seen.append((w, d, t)),
    )
    per_well: dict[str, list[int]] = {}
    for well, done, total in seen:
        assert total == 100                     # reshaped onto whole percent
        per_well.setdefault(well, []).append(done)

    assert set(per_well) == {"A01", "A02"}
    for well, steps in per_well.items():
        assert len(steps) <= 101, (well, len(steps))    # 240 raw ticks, deduped
        assert steps == sorted(steps), well             # monotonic, never resets
        assert len(steps) == len(set(steps)), well      # no repeated step
        assert steps[-1] == 100, well                   # ends full


# ── incomplete sources ───────────────────────────────────────────────────────
#
# tifffile sizes an OME series' page list from the dimensions the metadata
# declares and leaves a slot None wherever the file holds no IFD for it, then
# zero-fills those slots in ``series.asarray()``. The exploder reads pages one at
# a time, so a gap used to surface as ``AttributeError: 'NoneType' object has no
# attribute 'asarray'`` part-way through a run, after earlier wells had been
# written. These pin the refusal that replaced it.


def _write_truncated_packed(path, *, declared_frames, written_frames, size_c=1):
    """A single-<Image> OME-TIFF declaring more frames than the file contains.

    The shape an acquisition stopped part-way leaves behind: the OME-XML carries
    the planned ``SizeT`` while only the frames that were actually acquired have
    an IFD. Written by hand rather than through ``tifffile``'s ``ome=True``
    writer, which only ever emits self-consistent metadata.
    """
    planes = "".join(
        f'<TiffData FirstT="{i // size_c}" FirstC="{i % size_c}" FirstZ="0" '
        f'IFD="{i}" PlaneCount="1"/>'
        for i in range(written_frames * size_c)
    )
    channels = "".join(
        f'<Channel ID="Channel:0:{c}" Name="ch{c}" SamplesPerPixel="1"/>'
        for c in range(size_c)
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">'
        '<Image ID="Image:0" Name="scan">'
        '<Pixels ID="Pixels:0" DimensionOrder="XYZCT" Type="uint16" '
        f'SizeX="6" SizeY="6" SizeC="{size_c}" SizeZ="1" SizeT="{declared_frames}">'
        f'{channels}{planes}</Pixels></Image></OME>'
    )
    data = np.zeros((written_frames * size_c, 6, 6), np.uint16)
    tifffile.imwrite(str(path), data, description=xml, metadata=None,
                     photometric="minisblack")
    return path


def test_layout_counts_planes_the_file_does_not_contain(tmp_path):
    p = _write_truncated_packed(tmp_path / "cut.ome.tif",
                                declared_frames=6, written_frames=4)
    layout = read_ome_layout(p)
    assert layout.total_planes == 6
    assert layout.missing_planes == 2
    assert layout.first_missing_plane == 4
    assert layout.missing_plane_report("cut.ome.tif")


def test_complete_file_reports_no_missing_planes(tmp_path):
    layout = read_ome_layout(_write_ome(tmp_path / "s.ome.tif", positions=4, size_t=2))
    assert layout.missing_planes == 0
    assert layout.incomplete_positions == []
    assert layout.first_missing_plane == -1
    assert layout.missing_plane_report("s.ome.tif") == ""


def test_incomplete_file_rejected_before_any_zip_is_written(tmp_path):
    p = _write_truncated_packed(tmp_path / "cut.ome.tif",
                                declared_frames=6, written_frames=4)
    out = tmp_path / "in"
    with pytest.raises(ValueError) as exc:
        explode_ome_tiff(p, out, grid_rows=1, grid_cols=6, experiment="exp")
    # The message names the shortfall rather than the AttributeError the missing
    # page used to raise, and nothing was written for the wells that did resolve.
    assert "no image data" in str(exc.value)
    assert "cut.ome.tif" in str(exc.value)
    assert _zips(out) == []


def test_incomplete_multi_image_file_names_its_positions(tmp_path):
    """A gap in a multi-image file is refused too, and it names the positions.

    This is the quieter half of the bug: ``series.asarray()`` zero-fills a
    missing page instead of raising, so the multi-image path wrote blank images
    for those planes with nothing on them saying so.
    """
    layout = OmeLayout(
        n_positions=6, size_t=1, size_c=1, size_z=1,
        channel_names=["GFP"], timepoint_tokens=["t0"], axes_per_series=["YX"],
        total_planes=6, missing_planes=2, incomplete_positions=[4, 5],
        first_missing_plane=0,
    )
    report = layout.missing_plane_report("plate.ome.tif")
    assert "stage position(s): 4, 5" in report
    assert "blank images" in report


def test_missing_companion_files_are_named(tmp_path):
    """A multi-file OME whose companions are absent gets the actionable cause."""
    xml = (
        '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06"><Image>'
        '<Pixels><TiffData><UUID FileName="scan.ome.tif">uuid:a</UUID></TiffData>'
        '<TiffData><UUID FileName="scan_2.ome.tif">uuid:b</UUID></TiffData>'
        '<TiffData><UUID FileName="scan_3.ome.tif">uuid:c</UUID></TiffData>'
        '</Pixels></Image></OME>'
    )
    (tmp_path / "scan.ome.tif").write_bytes(b"")
    (tmp_path / "scan_2.ome.tif").write_bytes(b"")
    assert _missing_companion_files(xml, tmp_path / "scan.ome.tif") == ["scan_3.ome.tif"]

    layout = OmeLayout(
        n_positions=1, size_t=0, size_c=1, size_z=1,
        channel_names=["GFP"], timepoint_tokens=[], axes_per_series=["TYX"],
        single_image=True, n_frames=6, total_planes=6, missing_planes=2,
        incomplete_positions=[0], first_missing_plane=4,
        missing_files=["scan_3.ome.tif"],
    )
    report = layout.missing_plane_report("scan.ome.tif")
    assert "scan_3.ome.tif" in report
    assert "same folder" in report


def test_packed_plane_states_which_plane_is_missing(tmp_path):
    """The per-plane guard behind the pre-flight check: a stated failure, not
    ``AttributeError: 'NoneType' object has no attribute 'asarray'``."""

    class _Series:
        shape = (4, 6, 6)
        pages = [object(), None, object(), object()]

    with pytest.raises(ValueError) as exc:
        _packed_plane(_Series(), "TYX", 1, 0)
    assert "frame 1" in str(exc.value)
    assert "no image data" in str(exc.value)


# ── validating the source before anything is written ─────────────────────────


def _write_ome_xml(path, xml, n_pages, *, shape=(6, 6)):
    """A TIFF carrying *xml* verbatim as its description, with *n_pages* pages."""
    data = np.stack([np.full(shape, i + 1, np.uint16) for i in range(n_pages)])
    tifffile.imwrite(str(path), data, description=xml, metadata=None,
                     photometric="minisblack")
    return path


def _ome_xml(*, size_t, size_c=1, tiffdata=None, planes=""):
    entries = tiffdata if tiffdata is not None else [
        (t, c, t * size_c + c) for t in range(size_t) for c in range(size_c)]
    mapping = "".join(
        f'<TiffData FirstT="{t}" FirstC="{c}" FirstZ="0" IFD="{ifd}" PlaneCount="1"/>'
        for t, c, ifd in entries)
    chans = "".join(f'<Channel ID="Channel:0:{c}" Name="ch{c}" SamplesPerPixel="1"/>'
                    for c in range(size_c))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">'
        '<Image ID="Image:0" Name="scan"><Pixels ID="Pixels:0" '
        'DimensionOrder="XYZCT" Type="uint16" SizeX="6" SizeY="6" '
        f'SizeC="{size_c}" SizeZ="1" SizeT="{size_t}">{chans}{planes}{mapping}'
        '</Pixels></Image></OME>'
    )


def test_a_named_ome_tiff_with_no_readable_metadata_is_refused(tmp_path):
    """The shape an interrupted copy leaves: a structurally valid TIFF holding a
    fraction of the plate, whose description tag points past its own end. It
    reads as a one-frame file and would convert, silently, into one well."""
    # Big enough that tifffile writes the OME description after the pixel data,
    # which is what puts its offset past the end of a shortened copy — the same
    # shape a real interrupted transfer of a plate-sized file leaves.
    good = tmp_path / "full.ome.tif"
    tifffile.imwrite(str(good), np.zeros((40, 64, 64), np.uint16), ome=True,
                     photometric="minisblack",
                     metadata={"axes": "TYX"})
    cut = tmp_path / "cut.ome.tif"
    cut.write_bytes(good.read_bytes()[: int(good.stat().st_size * 0.6)])

    layout = read_ome_layout(cut)
    assert not layout.has_ome_xml
    assert layout.named_ome
    assert layout.warnings                       # tifffile said so; we kept it
    report = layout.validation_report("cut.ome.tif")
    assert "no readable OME metadata" in report
    with pytest.raises(ValueError, match="no readable OME metadata"):
        explode_ome_tiff(cut, tmp_path / "in", grid_rows=1, grid_cols=1)


def test_a_plain_tiff_stack_is_still_accepted(tmp_path):
    """A stack that never claimed to be an OME-TIFF is a legitimate input, and
    the "no OME metadata" refusal must not catch it."""
    p = tmp_path / "stack.tif"
    tifffile.imwrite(str(p), np.zeros((6, 6, 6), np.uint16), photometric="minisblack")
    layout = read_ome_layout(p)
    assert not layout.named_ome
    assert layout.validation_report("stack.tif") == ""


def test_ome_metadata_that_does_not_describe_the_pages_is_refused(tmp_path):
    """No TiffData at all: tifffile declines the OME structure and falls back to
    a plain page sequence, so every plane's channel and timepoint is a guess."""
    xml = _ome_xml(size_t=6, tiffdata=[])
    p = _write_ome_xml(tmp_path / "nomap.ome.tif", xml, 6)
    layout = read_ome_layout(p)
    assert layout.has_ome_xml
    assert layout.series_kind != "ome"
    assert "does not describe its pages" in layout.validation_report("nomap.ome.tif")


def test_a_broken_index_map_is_not_reported_as_a_truncated_run(tmp_path):
    """Every plane is in the file; the metadata points two of them at IFDs that
    do not exist. Telling this user to re-export a shorter file would throw away
    data that is present, so the count of unreferenced pages picks the cause."""
    xml = _ome_xml(size_t=6, tiffdata=[(0, 0, 0), (1, 0, 1), (2, 0, 2),
                                       (3, 0, 3), (4, 0, 8), (5, 0, 9)])
    p = _write_ome_xml(tmp_path / "badmap.ome.tif", xml, 6)
    layout = read_ome_layout(p)
    assert layout.missing_planes == 2
    assert layout.unreferenced_pages == 2
    report = layout.validation_report("badmap.ome.tif")
    assert "No image data is missing" in report
    assert "index map" in report
    assert "acquisition was stopped" not in report


def test_a_truncated_acquisition_still_reads_as_one(tmp_path):
    """The counterpart: fewer pages than declared, and every page referenced."""
    xml = _ome_xml(size_t=6, tiffdata=[(t, 0, t) for t in range(4)])
    p = _write_ome_xml(tmp_path / "short.ome.tif", xml, 4)
    layout = read_ome_layout(p)
    assert layout.missing_planes == 2
    assert layout.unreferenced_pages == 0
    report = layout.validation_report("short.ome.tif")
    assert "acquisition was stopped" in report
    assert "index map" not in report


def test_stage_positions_are_read_from_the_plane_elements(tmp_path):
    planes = "".join(
        f'<Plane TheT="{i}" TheC="0" TheZ="0" PositionX="{i * 10}" '
        f'PositionY="{i * 20}"/>' for i in range(4))
    p = _write_ome_xml(tmp_path / "pos.ome.tif",
                       _ome_xml(size_t=4, planes=planes), 4)
    layout = read_ome_layout(p)
    assert layout.stage_positions == [(0.0, 0.0), (10.0, 20.0),
                                      (20.0, 40.0), (30.0, 60.0)]


def test_a_sound_file_reports_nothing(tmp_path):
    p = _write_ome(tmp_path / "s.ome.tif", positions=4, size_t=2)
    layout = read_ome_layout(p)
    assert layout.validation_report("s.ome.tif") == ""
    assert layout.warnings == []
