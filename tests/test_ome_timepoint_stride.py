"""Feature parity with the CZI converter, on the OME-TIFF side.

Two things ported here, and one that is deliberately *not* a port: the
elapsed-hour rendering is now shared between the three converters rather than
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

from pipeline.explode_common import stride_note
from pipeline.timepoints import (
    _rounding_unit,
    elapsed_hour_tokens,
    regularise_hours,
)
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


# ── what the adversarial review of the merged branch found ───────────────────

def test_a_fully_resumed_strided_run_reports_100_percent(tmp_path):
    """The resumed-well loop bumped the progress counter once per *source*
    timepoint while the total counted the kept ones, so a stride-3 run that
    resumed every well finished at 300%. The CZI exploder's equivalent line
    already used the kept count."""
    src = _multi(tmp_path / "p.ome.tif", positions=2, timepoints=6)
    out = tmp_path / "out"
    explode_ome_tiff(src, out, grid_rows=1, grid_cols=2, experiment="Exp",
                     timepoint_stride=3)
    seen: list[tuple[int, int]] = []
    again = explode_ome_tiff(src, out, grid_rows=1, grid_cols=2,
                             experiment="Exp", timepoint_stride=3, resume=True,
                             progress_fn=lambda d, t: seen.append((d, t)))
    assert sorted(again.wells_resumed) == ["A01", "A02"]
    assert seen[-1] == (8, 8), f"ended at {seen[-1]}"


def test_a_fully_resumed_run_counts_every_fov_of_every_well(tmp_path):
    """The same fix, on a plate with more than one field per well.

    The test above uses a 1x2 grid over 2 positions, so ``fovs`` is 1 and the
    factor is invisible: dropping ``fovs`` from the resumed-well bump left it
    green, and left a real 2-well 4-FOV plate reporting 25%. A count that is
    right only when one of its factors happens to be 1 is not measured at all
    (CLAUDE.md §7.8 rule 2 — build the fixture so right and wrong differ).
    """
    src = _multi(tmp_path / "p.ome.tif", positions=8, timepoints=6)
    out = tmp_path / "out"
    # 8 positions over a 1x2 grid is 4 fields of view in each of 2 wells.
    kwargs = dict(grid_rows=1, grid_cols=2, experiment="Exp",
                  timepoint_stride=3)
    explode_ome_tiff(src, out, **kwargs)
    seen: list[tuple[int, int]] = []
    again = explode_ome_tiff(src, out, resume=True,
                             progress_fn=lambda d, t: seen.append((d, t)),
                             **kwargs)

    assert sorted(again.wells_resumed) == ["A01", "A02"]
    # 2 wells x 4 FOVs x 2 kept timepoints x 2 channels = 32.
    assert seen[-1] == (32, 32), f"ended at {seen[-1]}"


def test_a_twelve_and_a_half_minute_run_is_not_rounded_into_drift():
    """Rounding an *interval* and multiplying it by an index multiplies the
    rounding error too. 12.5 minutes rounded to the whole minute is 13, and by
    frame 19 that is 9.5 minutes of elapsed time the acquisition never
    recorded — written into a filename with nothing saying so."""
    raw = {i: i * 12.5 / 60.0 for i in range(20)}
    hours, note = regularise_hours(raw, 20)
    assert note == ""
    assert hours[19] == pytest.approx(19 * 12.5 / 60.0), (
        f"frame 19 drifted to {hours[19] * 60:.2f} min")
    assert elapsed_hour_tokens(raw, 20).tokens[1] == "0.208333h"


@pytest.mark.parametrize("minutes, frames", [
    # The shape the defect really had: an interval above the old ten-minute
    # rounding threshold *and* carrying a fractional minute. Measured against
    # the pre-fix code these drifted 9.5, 14.5 and 5.5 minutes respectively.
    (12.5, 20), (20.5, 30), (37.5, 12),
    # Below the threshold, so never rounded and never drifted — here to keep
    # them that way rather than to catch the original bug.
    (7.5, 40), (2.5, 100), (0.5, 60),
    # Above it but already a whole minute, so rounding was a no-op.
    (90.0, 8), (11.0, 25),
    # **The same designs shifted by seconds of stage overhead**, which is the
    # half the first eight could not measure: every one of them is exactly
    # representable in the unit the code picks, so the right and the wrong
    # answers agreed and the parametrize passed either way (CLAUDE.md §7.8
    # rule 2). Measured against the code these rows were added to, before the
    # budget was rewritten, they drifted 1.75, 0.63 and 4.75 minutes.
    (90.25, 8), (12.616666666666667, 20), (241.75, 20),
    # A long interval, where a budget derived from the interval licensed the
    # worst fabrication of all — 56 minutes at the last frame.
    (2880.0, 24), (1440.0, 7), (10080.0, 10),
])
def test_no_series_is_named_further_from_its_readings_than_the_budget(
        minutes, frames):
    """No timepoint is ever named further than a minute from its reading.

    The bound is absolute and does not scale with the interval, which is the
    whole point: every version that scaled it permitted the most fabrication
    exactly where the absolute error was largest. Asserted against
    the **raw series itself**, never by asking the code what it thinks its own
    budget is. A test that recomputed the bound the same way the
    implementation does would pass for a wrong bound; this one measures the
    jitter present in the readings and holds the output to it.

    The bound: a reading may move by the series' own gap jitter, or by one
    rounding unit, whichever is larger. Every one of these fixtures is
    perfectly regular — jitter is exactly zero — so the whole allowance is the
    rounding unit, and anything beyond it is elapsed time the acquisition
    never recorded.
    """
    raw = {i: i * minutes / 60.0 for i in range(frames)}
    hours, _note = regularise_hours(raw, frames)

    gaps = [raw[i + 1] - raw[i] for i in range(frames - 1)]
    jitter = max(gaps) - min(gaps)
    allowed = max(jitter, _rounding_unit(minutes / 60.0))
    worst = max(abs(hours[i] - raw[i]) for i in range(frames))

    assert worst <= allowed + 1e-12, (
        f"{minutes} min x {frames}: named {worst * 60:.3f} min from the "
        f"readings; its own jitter is {jitter * 60:.3f} min and one rounding "
        f"unit is {_rounding_unit(minutes / 60.0) * 60:.3f} min")


def test_a_perfectly_regular_series_is_never_moved_by_a_whole_unit():
    """A series with no jitter has nothing to tidy.

    Separate from the budget test above because they measure different things
    and one used to stand in for the other: a zero-jitter 238.75-minute series
    was named 4.75 minutes away from its own readings while every assertion
    passed, because the rows chosen all happened to be whole minutes.
    """
    raw = {i: i * 238.75 / 60.0 for i in range(20)}
    hours, _note = regularise_hours(raw, 20)
    worst = max(abs(hours[i] - raw[i]) for i in range(20))
    unit_min = _rounding_unit(238.75 / 60.0) * 60
    assert worst * 60 <= unit_min + 1e-9, (
        f"a series with no jitter was moved {worst * 60:.3f} min, which is "
        f"more than the {unit_min:.3f} min of rounding that is all it could "
        f"honestly be offered")


def test_jitter_is_still_cleaned_off_a_four_hourly_run():
    """Five and ten seconds of stage jitter still names 0h, 4h, 8h."""
    raw = {0: 0.0, 1: 4.0 + 5 / 3600, 2: 8.0 + 10 / 3600}
    assert elapsed_hour_tokens(raw, 3).tokens == ["0h", "4h", "8h"]


def test_jitter_is_still_cleaned_off_a_run_long_enough_to_accumulate():
    """The case the three-frame test above could not reach.

    It was named "hourly", used a *four*-hourly series, and used three frames
    — the shortest series with a gap at all, and the only length at which a
    rounding unit divided by the gap count still survived. So it passed
    before the fix, after it, and against the regression in between, where an
    ordinary twelve-frame hourly plate silently stopped being tidied and its
    archives carried ``11.0153h`` in their filenames.

    Twelve frames of an hourly run with five seconds of stage overhead per
    round: every timepoint must still be a whole hour.
    """
    raw = {i: i * (1.0 + 5 / 3600.0) for i in range(12)}

    tokens = elapsed_hour_tokens(raw, 12).tokens

    assert tokens == [f"{i}h" for i in range(12)]


def test_a_plate_imaged_once_is_not_told_it_lost_its_clock():
    """A single-timepoint plate is not a time-lapse whose elapsed times were
    dropped, so the page has nothing to warn about. The fallback note fired on
    every ordinary non-timelapse plate."""
    resolved = elapsed_hour_tokens({}, 1)
    assert resolved.tokens is None
    assert resolved.reasons == []


@pytest.mark.parametrize("stride, ordinal", [
    (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"),
    (13, "13th"), (21, "21st"), (22, "22nd"), (23, "23rd"), (101, "101st"),
])
def test_the_stride_note_spells_its_ordinal(stride, ordinal):
    """The teens are why this is not a table on the last digit: 11, 12 and 13
    take `th` despite ending in 1, 2 and 3."""
    note = stride_note(stride, 200, ["t0", f"t{stride}"])
    assert f"every {ordinal} of 200" in note, note


def test_the_snap_is_checked_against_the_readings_and_not_only_gap_by_gap():
    """The check ``regularise_hours`` calls load-bearing, pinned at last.

    Deleting it left all ninety-five tests in the three files that touch this
    math green, which is how a series that is regular at every gap and drifts
    far from a single interval over twenty of them went on being named at that
    interval. The per-gap test cannot see accumulation; only comparing the
    named series with the readings can.

    The fixture is built so the two answers differ: every gap is identical, so
    the gap-by-gap test passes and raises no note, while the snapped series
    lands well beyond the budget at the last frame.
    """
    interval_h = 4.0 + 105 / 3600.0          # 4 h plus 105 s of stage overhead
    frames = 20
    raw = {i: i * interval_h for i in range(frames)}

    hours, note = regularise_hours(raw, frames)

    # Regular gap by gap: no irregularity note, so nothing else declined it.
    assert note == ""
    # And therefore named at what was recorded, not at a rounder interval.
    # Perfectly regular, so the only allowance is one rounding unit.
    allowed_min = _rounding_unit(interval_h) * 60
    drift_min = abs(hours[frames - 1] - raw[frames - 1]) * 60
    assert drift_min <= allowed_min + 1e-9, (
        f"the last frame is named {drift_min:.2f} min from its reading, "
        f"and one rounding unit is {allowed_min:.2f} min")
    # And the fixture really does distinguish the two answers: snapping to a
    # whole 4 h would put the last frame 35 minutes from its reading.
    snapped_min = abs((hours[0] + 4.0 * (frames - 1)) - raw[frames - 1]) * 60
    assert snapped_min > allowed_min, (
        "the fixture does not distinguish snapping from not snapping")
