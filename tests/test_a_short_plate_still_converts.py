"""A source missing some images converts, and says so everywhere it can.

A CZI that holds fewer images than its scenes, tiles, timepoints and channels
imply used to be **refused**, and refused twice: once by the page and again by
the run. On a split-scene export each file is read on its own, so one file
short of a plane took the whole plate with it — six hundred and sixty sound
wells lost for the sake of one — and the refusal was decided after the read,
so the hour spent getting there bought nothing at all.

A gap is also not always a fault. The count is the difference between the
distinct ``(scene, tile, timepoint, channel)`` a file holds and the rectangle
those axes imply, and a legitimate acquisition need not fill its rectangle: a
brightfield channel imaged once at the start of a time course is short by
every timepoint after the first, and is exactly what was intended.

So the conversion proceeds and the shortfall is reported. What it must never
do is proceed *quietly* — a well that is short and reads as complete is
CLAUDE.md §7's cardinal failure — so the same fact is stated in four places,
and each of them is asserted here:

1. on the page, **before** the run, while the source can still be re-exported;
2. in the run log, as the plane is skipped;
3. in the closing summary the page shows when the run ends;
4. in a plainly named file beside the converted data, for whoever opens the
   folder months later with none of this in their head.

The missing plane is skipped and never substituted. A zero-filled image is
indistinguishable from a real image of a dark field, which is the silent
substitution the whole rule is about.
"""

from __future__ import annotations

import zipfile

import pytest

from datasets.core.convert_marker import (
    GAP_RECORD,
    read_gap_record,
    write_gap_record,
)
from datasets.czi_import.apply import convert
from datasets.czi_import.czi_import_compute import (
    ConversionParams,
    plan_conversion,
    read_source,
)
from pipeline.explode_common import shortfall_summary
from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi


@pytest.fixture
def short_plate(tmp_path):
    """Two wells, two channels, with one channel of the first well absent."""
    tiles = plate_tiles(wells=2, fovs=1, timepoints=2, channels=2)
    tiles = [t for t in tiles if not (t.scene == 0 and t.c == 1 and t.t == 1)]
    return write_czi(
        tmp_path / "short.czi", tiles,
        xml=plate_metadata_xml(scene_wells={0: "A01", 1: "A02"},
                               channels=["DAPI", "GFP"]))


def _run(src, dest, log=None):
    params = ConversionParams(experiment="E")
    return convert(str(src), str(dest), params,
                   log=(log.append if log is not None else lambda _m: None),
                   is_cancelled=lambda: False,
                   progress=lambda _d, _t: None, well_progress=None)


# ── the data that does exist is converted ────────────────────────────────────

def test_every_well_converts_including_the_short_one(short_plate, tmp_path):
    dest = tmp_path / "out"

    _run(short_plate, dest)

    assert sorted(p.name for p in (dest / "in").glob("*.zip")) == [
        "A01.zip", "A02.zip"]
    with zipfile.ZipFile(dest / "in" / "A02.zip") as whole:
        assert len(whole.namelist()) == 4       # 2 timepoints x 2 channels
    with zipfile.ZipFile(dest / "in" / "A01.zip") as short:
        members = sorted(short.namelist())
    assert len(members) == 3, members


def test_the_missing_plane_is_absent_and_not_a_blank(short_plate, tmp_path):
    """Skipped, never substituted. A zero-filled image would be
    indistinguishable from a real dark field."""
    dest = tmp_path / "out"

    _run(short_plate, dest)

    with zipfile.ZipFile(dest / "in" / "A01.zip") as archive:
        names = sorted(archive.namelist())
    # The GFP plane of the second timepoint is the one the source lacks.
    assert not any("GFP" in name and name.endswith("_t1.tif")
                   for name in names), names
    assert any("GFP" in name for name in names), (
        "the channel's other timepoint must still be there")


# ── and the shortfall is said in every place it can be said ──────────────────

def test_the_page_says_it_before_the_run(short_plate):
    """The half that stops an hour being wasted: as a refusal this news could
    only ever arrive at the end of the read."""
    params = ConversionParams(experiment="E")

    plan = plan_conversion(read_source(short_plate, params), params)

    assert "are missing" in plan.shortfall_warning
    assert "short.czi" in plan.shortfall_warning


def test_the_run_log_names_the_well_as_it_skips(short_plate, tmp_path):
    lines: list[str] = []

    _run(short_plate, tmp_path / "out", log=lines)

    text = "\n".join(lines)
    assert "A01: no image data for field 0" in text, text
    assert "GFP" in text


def test_the_closing_summary_does_not_read_like_a_whole_plate(short_plate,
                                                              tmp_path):
    """A summary reporting only the total written would read exactly like a
    complete conversion, which is the thing being guarded against."""
    summary = _run(short_plate, tmp_path / "out")

    assert "1 image(s) had no data in the source" in summary
    assert "A01 (1)" in summary
    assert "are not complete" in summary


def test_the_dataset_carries_a_plainly_named_record(short_plate, tmp_path):
    """For whoever opens the folder in six months with none of this in their
    head. Beside the data and not inside the marker, whose contents decide
    whether a later run may top this dataset up."""
    dest = tmp_path / "out"

    _run(short_plate, dest)

    record = dest / GAP_RECORD
    assert record.is_file(), f"{GAP_RECORD} was not written"
    body = record.read_text()
    assert "NOT complete" in body
    assert "A01\t1" in body
    assert read_gap_record(dest) == {"A01": 1}


# ── and says nothing at all when there is nothing to say ─────────────────────

def test_a_whole_plate_leaves_no_record_and_no_warning(tmp_path):
    """Seeing the record has to mean something, so it is absent when the
    conversion was complete (§7.8: "it warns" is satisfied by a page that
    always warns)."""
    src = write_czi(tmp_path / "whole.czi",
                    plate_tiles(wells=2, fovs=1, timepoints=2, channels=2),
                    xml=plate_metadata_xml(scene_wells={0: "A01", 1: "A02"},
                                           channels=["DAPI", "GFP"]))
    dest = tmp_path / "out"

    summary = _run(src, dest)

    assert not (dest / GAP_RECORD).exists()
    assert "had no data" not in summary
    params = ConversionParams(experiment="E")
    assert plan_conversion(read_source(src, params), params).shortfall_warning == ""


def test_a_top_up_run_does_not_erase_what_an_earlier_run_recorded(tmp_path):
    """A conversion can add wells to a dataset it did not create. A top-up
    knows only about the wells it converted, so writing its own findings over
    the record would erase the first half of the plate's gaps — leaving a
    dataset that holds a short well beside a record saying every well is
    whole, which is worse than having no record.

    Driven through the record itself rather than through two conversions,
    because the destination check keys on each source file's size and
    modification time: a source edited between runs is refused as a different
    plate, so the two-conversion route cannot reach this.
    """
    dest = tmp_path / "out"
    dest.mkdir()

    write_gap_record(dest, {"A01": 2}, ["A01", "A02"])
    write_gap_record(dest, {}, ["H11", "H12"])      # the top-up's own wells

    assert read_gap_record(dest) == {"A01": 2}, (
        "the top-up erased what the first run recorded")


def test_a_well_converted_again_and_now_whole_loses_its_entry(tmp_path):
    """The other direction, and the reason the entries are keyed on the wells
    a run touched rather than merged blindly: a record that could only ever
    grow would keep claiming a shortfall that had been fixed."""
    dest = tmp_path / "out"
    dest.mkdir()

    write_gap_record(dest, {"A01": 2, "B02": 1}, ["A01", "B02"])
    write_gap_record(dest, {}, ["A01"])

    assert read_gap_record(dest) == {"B02": 1}


def test_the_record_goes_when_nothing_is_left_to_say(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    write_gap_record(dest, {"A01": 1}, ["A01"])
    assert (dest / GAP_RECORD).is_file()

    write_gap_record(dest, {}, ["A01"])

    assert not (dest / GAP_RECORD).exists()


def test_the_summary_sentence_is_written_once_and_shared():
    """The log, the closing summary and the record all say the same thing, so
    they say it with the same function — three spellings of one sentence is
    how a log and an artifact come to disagree about what happened."""
    assert shortfall_summary({}) == ""
    one = shortfall_summary({"C07": 3})
    assert "C07 (3)" in one and "3 image(s)" in one
    many = shortfall_summary({"H11": 1, "A02": 2})
    # Plate order, not whichever worker finished first, so two runs of one
    # plate say the same thing.
    assert many.index("A02") < many.index("H11")
    assert "3 image(s)" in many and "2 well(s)" in many
