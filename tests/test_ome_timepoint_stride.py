"""Feature parity with the CZI converter, on the OME-TIFF side.

Two things ported here, and one that is deliberately *not* a port: the
elapsed-hour rendering is now shared between the two converters rather than
copied, so this file also pins the properties that only show up once the same
code names both a CZI's stamps and an OME-TIFF's ``DeltaT``.

The single-image case carries a trap the CZI side does not have. A packed
OME-TIFF's frames are one flat sequence, and ``_frame_index`` locates a plane
with ``position × n_timepoints + t``, where ``n_timepoints`` is what the *file*
holds. A stride that rebound that name to the kept count would read the wrong
frame for every position after the first — and would do it silently, since the
member names and the archive sizes would all still be right.
"""

from __future__ import annotations

import zipfile

import numpy as np
import pytest
import tifffile

from pipeline.explode_common import elapsed_hour_tokens, regularise_hours
from pipeline.ome_explode import explode_ome_tiff


def _packed(path, *, positions, timepoints, channels=("DAPI", "GFP"), delta_s=None):
    """A single-image OME-TIFF: one series whose T axis is position × time.

    Each frame's pixels are filled with ``position * 100 + timepoint``, so a
    frame read from the wrong index is a *different number* rather than a
    different-looking image.
    """
    frames = positions * timepoints
    data = np.zeros((frames, len(channels), 6, 6), np.uint16)
    for pos in range(positions):
        for t in range(timepoints):
            data[pos * timepoints + t, :, :, :] = pos * 100 + t
    meta = {"axes": "TCYX", "Channel": {"Name": list(channels)}}
    if delta_s is not None:
        meta["Plane"] = [{"DeltaT": float(delta_s[i % timepoints]),
                          "DeltaTUnit": "s"} for i in range(frames)]
    tifffile.imwrite(str(path), data, ome=True, photometric="minisblack",
                     metadata=meta)
    return path


def _multi(path, *, positions, timepoints, channels=("DAPI", "GFP"), delta_s=None):
    """A multi-image OME-TIFF: one series per stage position."""
    with tifffile.TiffWriter(str(path), ome=True) as tw:
        for _pos in range(positions):
            arr = np.zeros((timepoints, len(channels), 6, 6), np.uint16)
            for t in range(timepoints):
                arr[t, :, :, :] = t
            meta = {"axes": "TCYX", "Channel": {"Name": list(channels)}}
            if delta_s is not None:
                meta["Plane"] = [{"DeltaT": float(delta_s[t]),
                                  "DeltaTUnit": "s"}
                                 for t in range(timepoints)
                                 for _c in channels]
            tw.write(arr, photometric="minisblack", metadata=meta)
    return path


def _names(out, well):
    return sorted(zipfile.ZipFile(out / f"{well}.zip").namelist())


# ── the stride ───────────────────────────────────────────────────────────────

def test_a_stride_writes_only_the_kept_timepoints(tmp_path):
    src = _multi(tmp_path / "p.ome.tif", positions=2, timepoints=6)
    out = tmp_path / "out"
    result = explode_ome_tiff(src, out, grid_rows=1, grid_cols=2,
                              experiment="Exp", timepoint_stride=2)
    assert result.timepoints == ["t0", "t2", "t4"]
    assert _names(out, "A01") == [
        "Exp_DAPI_A01_f0_t0.tif", "Exp_DAPI_A01_f0_t2.tif",
        "Exp_DAPI_A01_f0_t4.tif", "Exp_GFP_A01_f0_t0.tif",
        "Exp_GFP_A01_f0_t2.tif", "Exp_GFP_A01_f0_t4.tif"]


def test_a_stride_of_one_matches_no_stride_at_all(tmp_path):
    src = _multi(tmp_path / "p.ome.tif", positions=2, timepoints=4)
    plain = explode_ome_tiff(src, tmp_path / "a", grid_rows=1, grid_cols=2,
                             experiment="Exp")
    strided = explode_ome_tiff(src, tmp_path / "b", grid_rows=1, grid_cols=2,
                               experiment="Exp", timepoint_stride=1)
    assert plain.timepoints == strided.timepoints
    assert _names(tmp_path / "a", "A01") == _names(tmp_path / "b", "A01")


def test_a_single_image_stride_still_reads_the_right_frame(tmp_path):
    """The trap in this file's docstring. Position 1's `t2` sits at frame 5 of
    the file's flat sequence; a stride that rebound `n_timepoints` to the kept
    count would look for it at frame 3 — position 0's `t3` — and write that
    image under position 1's name with nothing raising."""
    src = _packed(tmp_path / "p.ome.tif", positions=2, timepoints=3)
    out = tmp_path / "out"
    explode_ome_tiff(src, out, grid_rows=1, grid_cols=2, experiment="Exp",
                     fovs_per_well=1, frame_order="time_in_pos",
                     timepoint_stride=2)
    with zipfile.ZipFile(out / "A02.zip") as archive:
        with archive.open("Exp_DAPI_A02_f0_t2.tif") as fh:
            plane = tifffile.imread(fh)
    # position 1, timepoint 2 → 1 * 100 + 2
    assert int(np.unique(plane)[0]) == 102


def test_a_bad_stride_is_refused_before_anything_is_written(tmp_path):
    src = _multi(tmp_path / "p.ome.tif", positions=2, timepoints=4)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="stride must be 1 or more"):
        explode_ome_tiff(src, out, grid_rows=1, grid_cols=2, experiment="Exp",
                         timepoint_stride=0)
    assert not (out / "A01.zip").exists()


def test_the_run_log_says_which_timepoints_a_stride_kept(tmp_path):
    src = _multi(tmp_path / "p.ome.tif", positions=2, timepoints=6)
    lines: list[str] = []
    explode_ome_tiff(src, tmp_path / "out", grid_rows=1, grid_cols=2,
                     experiment="Exp", timepoint_stride=3, log_fn=lines.append)
    opening = next(line for line in lines if "well(s)" in line)
    assert "2 timepoint(s) (every 3rd of 6: t0, t3)" in opening, opening


def test_resuming_a_strided_run_leaves_its_own_wells_alone(tmp_path):
    src = _multi(tmp_path / "p.ome.tif", positions=2, timepoints=6)
    out = tmp_path / "out"
    explode_ome_tiff(src, out, grid_rows=1, grid_cols=2, experiment="Exp",
                     timepoint_stride=2)
    again = explode_ome_tiff(src, out, grid_rows=1, grid_cols=2,
                             experiment="Exp", timepoint_stride=2, resume=True)
    assert sorted(again.wells_resumed) == ["A01", "A02"]
    assert again.images_written == 0


# ── elapsed hours, now shared with the CZI converter ─────────────────────────

def test_jittery_hourly_times_are_named_at_the_interval_they_ran_at(tmp_path):
    """A real four-hourly run reads 0, 4.00139, 8.00278 h because the stage
    takes as long as it takes. Naming those literally puts `4.00139h` in a
    filename — true, unhelpful, and different on the next plate."""
    deltas = [0.0, 4.0 * 3600 + 5, 8.0 * 3600 + 10]
    src = _multi(tmp_path / "p.ome.tif", positions=2, timepoints=3,
                 delta_s=deltas)
    out = tmp_path / "out"
    result = explode_ome_tiff(src, out, grid_rows=1, grid_cols=2,
                              experiment="Exp")
    assert result.timepoints == ["0h", "4h", "8h"]


def test_a_regular_run_is_named_at_exactly_one_interval(tmp_path):
    """Rounding to the minute is not the whole of it. A run whose drift
    survives that rounding — 70 s late, then 50 s late — still ran at one
    interval, and naming it at the times actually recorded gives `8.01667h`
    where the third image belongs at twice the second's offset. The fixture's
    drift *varies*, because a constant drift makes the snapped and the merely
    rounded answers identical and no assertion could tell them apart
    (CLAUDE.md §7.8 rule 2)."""
    raw = {0: 0.0, 1: (4 * 3600 + 70) / 3600, 2: (8 * 3600 + 50) / 3600}
    snapped, note = regularise_hours(raw, 3)
    assert note == ""
    assert snapped[2] == pytest.approx(2 * snapped[1]), (
        "the third timepoint is not at twice the interval")
    assert snapped[2] != pytest.approx(round(raw[2] * 60) / 60)


def test_an_unevenly_spaced_run_keeps_its_measured_times(tmp_path):
    """0, 4, 8 then an overnight point is an ordinary design and has no single
    interval. Forcing one would state three spacings that never happened."""
    hours, note = regularise_hours({0: 0.0, 1: 4.0, 2: 8.0, 3: 24.0}, 4)
    assert hours == {0: 0.0, 1: 4.0, 2: 8.0, 3: 24.0}
    assert "not evenly spaced" in note
    assert elapsed_hour_tokens({0: 0.0, 1: 4.0, 2: 8.0, 3: 24.0}, 4).tokens == [
        "0h", "4h", "8h", "24h"]


def test_a_sub_minute_timelapse_is_not_rounded_into_one_token(tmp_path):
    """Rounding to the minute is jitter-removal on an hourly run and total
    destruction on frames 100 ms apart, where every timepoint lands on `0h`,
    collides, and sends the whole run to positional names. OME-TIFFs carry
    millisecond DeltaT routinely."""
    hours = {i: i * 100e-3 / 3600.0 for i in range(6)}
    resolved = elapsed_hour_tokens(hours, 6)
    assert resolved.tokens is not None, resolved.reasons
    assert len(set(resolved.tokens)) == 6


def test_the_series_origin_is_not_moved_to_zero():
    """An OME `DeltaT` is elapsed from the *acquisition's* start, so a file
    whose first frame sits at one hour legitimately begins at `1h`. Snapping to
    `index × interval` would move every timepoint by that offset, invisibly,
    since the spacing it is checked against stays right."""
    hours, note = regularise_hours({0: 1.0, 1: 5.0, 2: 9.0}, 3)
    assert hours == {0: 1.0, 1: 5.0, 2: 9.0}
    assert note == ""
    assert elapsed_hour_tokens({0: 1.0, 1: 5.0, 2: 9.0}, 3).tokens == [
        "1h", "5h", "9h"]


def test_the_stride_selects_from_the_elapsed_tokens(tmp_path):
    """The two features compose: the stride picks which timepoints, the times
    name them. Every second point of a four-hourly run is eight hours apart."""
    deltas = [0.0, 4 * 3600.0, 8 * 3600.0, 12 * 3600.0]
    src = _multi(tmp_path / "p.ome.tif", positions=2, timepoints=4,
                 delta_s=deltas)
    result = explode_ome_tiff(src, tmp_path / "out", grid_rows=1, grid_cols=2,
                              experiment="Exp", timepoint_stride=2)
    assert result.timepoints == ["0h", "8h"]


def test_progress_is_reported_against_the_kept_timepoint_count(tmp_path):
    """A total taken from the source's timepoints leaves the bar stuck at a
    third full on a run that finished."""
    src = _multi(tmp_path / "p.ome.tif", positions=2, timepoints=6)
    seen: list[tuple[int, int]] = []
    explode_ome_tiff(src, tmp_path / "out", grid_rows=1, grid_cols=2,
                     experiment="Exp", timepoint_stride=3,
                     progress_fn=lambda done, total: seen.append((done, total)))
    # 2 positions x 2 kept timepoints x 2 channels
    assert seen[-1] == (8, 8), f"ended at {seen[-1]}"


def test_convert_passes_the_stride_through_to_the_archives(tmp_path):
    """``datasets.ome_import.apply.convert`` is the seam a queued job and the
    page both go through. A stride the page collects and ``convert`` drops
    reaches nothing (CLAUDE.md §7.4 #1: assert at the boundary)."""
    from datasets.ome_import.apply import convert
    from datasets.ome_import.ome_import_compute import ConversionParams

    src = _multi(tmp_path / "p.ome.tif", positions=2, timepoints=6)
    dest = tmp_path / "dest"
    convert(src, dest, ConversionParams(grid_rows=1, grid_cols=2,
                                        experiment="Exp", timepoint_stride=3))
    assert _names(dest / "in", "A01") == [
        "Exp_DAPI_A01_f0_t0.tif", "Exp_DAPI_A01_f0_t3.tif",
        "Exp_GFP_A01_f0_t0.tif", "Exp_GFP_A01_f0_t3.tif"]
