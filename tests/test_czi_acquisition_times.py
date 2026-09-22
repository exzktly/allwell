"""Naming a CZI's timepoints by elapsed time instead of by position.

A CZI stamps a plane's wall-clock acquisition time into each *subblock's* own
metadata, so the converter samples one position's series and measures the rest
from its first reading. Two things about that shape drive what is asserted
here.

**One position, not the plate.** A plate scan reaches its wells minutes apart
inside a single round, so a series assembled from whichever scene happened to
sort first at each timepoint folds the stage's traverse into the elapsed times.
Several tests below build a plate whose scenes are stamped deliberately out of
step for exactly that reason: a reader taking the wrong scene's clock produces
a *different number*, not merely a different-looking one.

**The fallback is the normal case, and it must say so.** No CZI in this
repository comes from ZEN, and the ``AcquisitionTime`` tag name is taken from
the format's documentation rather than from a file this repository can check
against. So every route to positional tokens is asserted to carry its reason,
because a plate named ``t0``/``t1`` because a tag could not be found has to be
distinguishable from one whose writer recorded no times at all.
"""

from __future__ import annotations

import zipfile

import pytest

from pipeline.czi_read import (
    normalise_instant,
    parse_acquisition_time,
    read_timepoint_hours,
)
from pipeline.czi_set import read_czi_set
from pipeline.explode_common import elapsed_hour_tokens, hours_token
from tests._czi_fixture import Tile, plate_metadata_xml, plate_tiles, write_czi

import numpy as np


def _stamp(hours: float, *, day: int = 12, fraction: str = "4830000") -> str:
    """An ISO 8601 instant *hours* after 10:00, spelled the way ZEN spells it."""
    total = round(hours * 3600)
    h, rem = divmod(10 * 3600 + total, 3600)
    m, sec = divmod(rem, 60)
    return f"2024-06-{day:02d}T{h:02d}:{m:02d}:{sec:02d}.{fraction}Z"


def _timed_plate(tmp_path, *, hours, scenes=2, scene_offset_h=0.5,
                 name="Plate.czi"):
    """A plate whose scenes are stamped deliberately out of step.

    Scene 1's readings sit *scene_offset_h* later than scene 0's — the traverse
    a real plate scan takes to cross from one well to the next. So a reader
    that mixes scenes, or takes the last scene rather than the first, reports
    times that differ from the intended ones by that offset.
    """
    tiles: list[Tile] = []
    for s in range(scenes):
        for t, elapsed in enumerate(hours):
            value = (s * 10 + t) * 10
            tiles.append(Tile(
                pixels=np.full((4, 6), value, dtype="uint16"),
                scene=s, tile=0, t=t, c=0,
                acquired=_stamp(elapsed + s * scene_offset_h)))
    return write_czi(
        tmp_path / name, tiles,
        xml=plate_metadata_xml(
            scene_wells={i: f"C{i + 1:02d}" for i in range(scenes)},
            channels=["DAPI"]))


# ── parsing one stamp ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("2024-06-12T10:23:45.4830000Z", "2024-06-12 10:23:45.483000+00:00"),
    ("2024-06-12T10:23:45.483Z", "2024-06-12 10:23:45.483000+00:00"),
    ("2024-06-12T10:23:45Z", "2024-06-12 10:23:45+00:00"),
    ("2024-06-12T10:23:45+02:00", "2024-06-12 10:23:45+02:00"),
])
def test_the_spellings_zen_writes_all_parse(text, expected):
    """Seven fractional digits is the one real files use and the one
    ``datetime.fromisoformat`` rejects on this Python."""
    xml = f"<METADATA><Tags><AcquisitionTime>{text}</AcquisitionTime></Tags></METADATA>"
    assert str(parse_acquisition_time(xml)) == expected


@pytest.mark.parametrize("raw, normalised", [
    # The two spellings ZEN writes that Python 3.10's fromisoformat rejects.
    # Asserted on the normaliser's own output: 3.11's parser accepts both
    # un-normalised, so an end-to-end parse passes whether or not the
    # rewriting happens, and could not tell the 3.10 build environment that
    # its plates were about to lose their elapsed times.
    ("2024-06-12T10:23:45.4830000Z", "2024-06-12T10:23:45.483000+00:00"),
    ("2024-06-12T10:23:45.483Z", "2024-06-12T10:23:45.483000+00:00"),
    ("2024-06-12T10:23:45Z", "2024-06-12T10:23:45+00:00"),
    ("2024-06-12T10:23:45.1+00:00", "2024-06-12T10:23:45.100000+00:00"),
    # Already acceptable; left exactly as it is.
    ("2024-06-12T10:23:45+02:00", "2024-06-12T10:23:45+02:00"),
    ("", ""),
])
def test_the_instant_is_rewritten_into_the_spelling_every_python_parses(
        raw, normalised):
    assert normalise_instant(raw) == normalised


@pytest.mark.parametrize("xml", [
    "",
    "not xml at all",
    "<METADATA><Tags><StageXPosition>1.0</StageXPosition></Tags></METADATA>",
    "<METADATA><Tags><AcquisitionTime>yesterday</AcquisitionTime></Tags></METADATA>",
    "<METADATA><Tags><AcquisitionTime></AcquisitionTime></Tags></METADATA>",
])
def test_anything_unreadable_is_none_rather_than_a_guess(xml):
    assert parse_acquisition_time(xml) is None


# ── sampling one position's series ───────────────────────────────────────────

def _blocks_of(path):
    from pipeline.czi_read import _read_file_header, _read_subblock_directory
    with open(path, "rb") as fh:
        directory, _meta = _read_file_header(fh)
        return [b for b in _read_subblock_directory(fh, directory)
                if not b.is_pyramid]


def test_the_elapsed_hours_come_from_the_first_scene_scanned(tmp_path):
    """Scene 1 is stamped half an hour later than scene 0 at every timepoint,
    so a reader taking scene 1's clock — or mixing the two — reports 0.5 h
    where this expects 0.0."""
    src = _timed_plate(tmp_path, hours=[0.0, 4.0, 8.0], scene_offset_h=0.5)
    scan = read_timepoint_hours(src, _blocks_of(src), 3)
    assert scan.scene == 0
    assert scan.hours_by_index == {0: 0.0, 1: 4.0, 2: 8.0}
    assert scan.reasons == []


def test_the_series_is_measured_from_its_own_first_reading(tmp_path):
    """The plate starts at 10:00 and the first timepoint reads 0 h, not 10 h —
    the stamps are absolute instants and the tokens are elapsed."""
    src = _timed_plate(tmp_path, hours=[0.0, 1.5])
    scan = read_timepoint_hours(src, _blocks_of(src), 2)
    assert scan.hours_by_index[0] == 0.0
    assert scan.hours_by_index[1] == pytest.approx(1.5)


def test_a_scene_missing_a_timepoint_is_not_the_one_chosen(tmp_path):
    """Filling its gap from another scene would splice two clocks together."""
    tiles = [
        Tile(pixels=np.full((4, 6), 1, dtype="uint16"), scene=0, t=0,
             acquired=_stamp(0.0)),
        # scene 0 has no t1 at all
        Tile(pixels=np.full((4, 6), 2, dtype="uint16"), scene=1, t=0,
             acquired=_stamp(0.5)),
        Tile(pixels=np.full((4, 6), 3, dtype="uint16"), scene=1, t=1,
             acquired=_stamp(4.5)),
    ]
    src = write_czi(tmp_path / "Ragged.czi", tiles)
    scan = read_timepoint_hours(src, _blocks_of(src), 2)
    assert scan.scene == 1
    assert scan.hours_by_index == {0: 0.0, 1: pytest.approx(4.0)}


def test_no_scene_covering_every_timepoint_declines_with_a_reason(tmp_path):
    tiles = [
        Tile(pixels=np.full((4, 6), 1, dtype="uint16"), scene=0, t=0,
             acquired=_stamp(0.0)),
        Tile(pixels=np.full((4, 6), 2, dtype="uint16"), scene=1, t=1,
             acquired=_stamp(4.0)),
    ]
    src = write_czi(tmp_path / "Split.czi", tiles)
    scan = read_timepoint_hours(src, _blocks_of(src), 2)
    assert scan.hours_by_index == {}
    assert "no single scene" in " ".join(scan.reasons)


def test_a_file_with_no_stamps_declines_and_says_how_many_it_found(tmp_path):
    tiles = plate_tiles(wells=1, fovs=1, timepoints=3, channels=1)
    src = write_czi(tmp_path / "Plain.czi", tiles)
    scan = read_timepoint_hours(src, _blocks_of(src), 3)
    assert scan.hours_by_index == {}
    assert "0 of 3" in " ".join(scan.reasons)


def test_a_missing_first_stamp_declines_rather_than_shifting_the_origin(tmp_path):
    """Measuring from t1 instead would report every timepoint as earlier than
    it was, with the run reading as though it started late."""
    tiles = [
        Tile(pixels=np.full((4, 6), 1, dtype="uint16"), scene=0, t=0),
        Tile(pixels=np.full((4, 6), 2, dtype="uint16"), scene=0, t=1,
             acquired=_stamp(4.0)),
    ]
    src = write_czi(tmp_path / "NoZero.czi", tiles)
    scan = read_timepoint_hours(src, _blocks_of(src), 2)
    assert scan.hours_by_index == {}
    assert "the first is not among them" in " ".join(scan.reasons)


def test_mixing_zoned_and_unzoned_instants_is_refused(tmp_path):
    """Subtracting one from the other raises, and the file has not said which
    clock either is on."""
    tiles = [
        Tile(pixels=np.full((4, 6), 1, dtype="uint16"), scene=0, t=0,
             acquired="2024-06-12T10:00:00.000Z"),
        Tile(pixels=np.full((4, 6), 2, dtype="uint16"), scene=0, t=1,
             acquired="2024-06-12T14:00:00.000"),
    ]
    src = write_czi(tmp_path / "Mixed.czi", tiles)
    scan = read_timepoint_hours(src, _blocks_of(src), 2)
    assert scan.hours_by_index == {}
    assert "zoned and unzoned" in " ".join(scan.reasons)


def test_sampling_reads_kilobytes_not_the_plate(tmp_path):
    """One seek and one short read per timepoint, whatever the plate weighs —
    the property that makes this affordable over a share at all."""
    src = _timed_plate(tmp_path, hours=[0.0, 4.0, 8.0, 12.0], scenes=8)
    scan = read_timepoint_hours(src, _blocks_of(src), 4)
    assert scan.hours_by_index
    assert scan.bytes_read < 32_768, scan.bytes_read


# ── rendering, shared with the OME converter ─────────────────────────────────

@pytest.mark.parametrize("hours, token", [
    (0.0, "0h"), (1.0, "1h"), (4.0, "4h"), (0.5, "0.5h"), (24.0, "24h"),
])
def test_elapsed_hours_render_the_way_the_ome_converter_renders_them(
        hours, token):
    assert hours_token(hours) == token


def test_two_timepoints_rendering_alike_fall_back_with_a_reason():
    """One member name for two timepoints: zipfile stores both and a reader
    returns only the last, so one timepoint silently vanishes."""
    resolved = elapsed_hour_tokens({0: 0.0, 1: 0.0, 2: 4.0}, 3)
    assert resolved.tokens is None
    assert "repeat" in " ".join(resolved.reasons)


def test_an_incomplete_series_falls_back_with_a_count():
    resolved = elapsed_hour_tokens({0: 0.0, 1: 4.0}, 3)
    assert resolved.tokens is None
    assert "2 of 3" in " ".join(resolved.reasons)


def test_a_negative_elapsed_time_falls_back_rather_than_being_written():
    """``util.timepoint`` accepts no sign, so such a token would not parse."""
    resolved = elapsed_hour_tokens({0: 0.0, 1: -4.0}, 2)
    assert resolved.tokens is None
    assert "not finite or not positive" in " ".join(resolved.reasons)


# ── the plate, and the archives ──────────────────────────────────────────────

def test_a_timed_plate_names_its_timepoints_by_elapsed_hours(tmp_path):
    src = _timed_plate(tmp_path, hours=[0.0, 4.0, 8.0])
    czi_set = read_czi_set(src)
    assert czi_set.timepoint_tokens == ["0h", "4h", "8h"]
    assert czi_set.timepoint_note == ""


def test_an_untimed_plate_stays_positional_and_says_why(tmp_path):
    src = write_czi(tmp_path / "Plain.czi",
                    plate_tiles(wells=2, fovs=1, timepoints=3, channels=1))
    czi_set = read_czi_set(src)
    assert czi_set.timepoint_tokens == ["t0", "t1", "t2"]
    assert "0 of 3" in czi_set.timepoint_note


def test_the_archives_carry_the_elapsed_tokens(tmp_path):
    from pipeline.czi_explode import explode_czi

    src = _timed_plate(tmp_path, hours=[0.0, 4.0, 8.0])
    out = tmp_path / "out"
    result = explode_czi(src, out, experiment="Exp")
    assert result.timepoints == ["0h", "4h", "8h"]
    assert sorted(zipfile.ZipFile(out / "C01.zip").namelist()) == [
        "Exp_DAPI_C01_f0_0h.tif", "Exp_DAPI_C01_f0_4h.tif",
        "Exp_DAPI_C01_f0_8h.tif"]


def test_the_stride_selects_from_the_elapsed_tokens(tmp_path):
    """The two features compose: the stride picks which timepoints, the stamps
    name them. Every second point of a four-hourly run is eight hours apart,
    and the tokens say so."""
    from pipeline.czi_explode import explode_czi

    src = _timed_plate(tmp_path, hours=[0.0, 4.0, 8.0, 12.0])
    result = explode_czi(src, tmp_path / "out", experiment="Exp",
                         timepoint_stride=2)
    assert result.timepoints == ["0h", "8h"]


def test_the_run_log_says_when_the_elapsed_times_were_not_usable(tmp_path):
    from pipeline.czi_explode import explode_czi

    src = write_czi(
        tmp_path / "Plain.czi",
        plate_tiles(wells=1, fovs=1, timepoints=3, channels=1),
        xml=plate_metadata_xml(scene_wells={0: "C01"}, channels=["DAPI"]))
    lines: list[str] = []
    explode_czi(src, tmp_path / "out", experiment="Exp", log_fn=lines.append)
    said = " ".join(lines)
    assert "named by position" in said and "0 of 3" in said


def test_only_the_first_file_of_a_split_export_is_sampled(tmp_path):
    """Every member covers the same timepoints, so sampling all of them would
    be N times the round trips for one answer — and the first file is the well
    the stage visited first."""
    folder = tmp_path / "split"
    folder.mkdir()
    for s in range(4):
        tiles = [Tile(pixels=np.full((4, 6), s * 10 + t, dtype="uint16"),
                      scene=0, t=t, acquired=_stamp(t * 4.0 + s * 0.25))
                 for t in range(3)]
        write_czi(folder / f"scan_s{s + 1}.czi", tiles,
                  xml=plate_metadata_xml(scene_wells={0: f"A{s + 1:02d}"},
                                         channels=["DAPI"]))
    czi_set = read_czi_set(folder)
    assert czi_set.timepoint_tokens == ["0h", "4h", "8h"]
    assert [lay.time_scan is not None for lay in czi_set.layouts] == [
        True, False, False, False]
