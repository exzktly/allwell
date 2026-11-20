"""Subset of a dataset that holds only ``in/``: a converted plate nobody has analysed yet.

The fixture is converter-shaped — ``in/<WELL>.zip`` holding ``<exp>_<channel>_<well>_<field>_<timepoint>.tif``
and, unless a test says otherwise, the ``in/acquisition.json`` a packed-file converter writes beside them. Every
assertion is made on what is on disk afterwards, because the archives and their sidecars are all a raw dataset is.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from datasets.core.convert_marker import DONE_MARKER, read_gap_record, write_gap_record
from datasets.subset.apply import execute_plan
from datasets.subset.apply_raw import STAGING_SUFFIX
from datasets.subset.raw_compute import FROM_INFERRED, FROM_RECORD, FROM_TYPED
from datasets.subset.subset_compute import (
    IN_PLACE_MARKER,
    SubsetError,
    SubsetFilter,
    build_subset_plan,
)
from pipeline.converter_record import TileSource, build_record
from util.acquisition_record import read_record, validate_against_archives, write_record
from util.nanowell_anchors import Anchor, anchors_path, read_anchors, write_anchors

WELLS = ("A01", "A02", "B01")
TPS = ("0h", "24h", "48h")
FIELDS = ("f0", "f1")
CHANNELS = ("DAPI", "GFP")


def _name(well, tp, field, channel, exp="E"):
    return f"{exp}_{channel}_{well}_{field}_{tp}.tif"


def _raw(root, *, wells=WELLS, timepoints=TPS, fields=FIELDS, channels=CHANNELS, record=True, namer=_name):
    (root / "in").mkdir(parents=True)
    for well in wells:
        with zipfile.ZipFile(root / "in" / f"{well}.zip", "w") as archive:
            for tp in timepoints:
                for field in fields:
                    for channel in channels:
                        archive.writestr(namer(well, tp, field, channel), f"{well}{field}{channel}{tp}".encode())
    if record:
        tiles = {(w, f, t): TileSource((1.0, 2.0), (0.5, 0.5), (8, 8))
                 for w in wells for f in fields for t in timepoints}
        write_record(root, build_record("plate.ome.tif", tiles))
    return root


def _members(root, well):
    with zipfile.ZipFile(root / "in" / f"{well}.zip") as archive:
        return sorted(archive.namelist())


def _expected(well, *, timepoints=TPS, fields=FIELDS, channels=CHANNELS):
    return sorted(_name(well, t, f, c) for t in timepoints for f in fields for c in channels)


def _run(src, dest, filt, **kwargs):
    plan = build_subset_plan(src, dest, filt, **kwargs)
    return plan, execute_plan(plan)


# ── the source is accepted, and its schema comes from the right place ────────
def test_an_in_only_dataset_is_subset_by_timepoint_from_its_acquisition_record(tmp_path):
    src = _raw(tmp_path / "src")
    plan, summary = _run(src, tmp_path / "dest", SubsetFilter(timepoint_labels=frozenset({"24h"})))
    assert plan.raw and plan.raw_source.origin == FROM_RECORD
    for well in WELLS:
        assert _members(tmp_path / "dest", well) == _expected(well, timepoints=("24h",))
    assert not (tmp_path / "dest" / "out").exists()          # still an unanalysed plate
    assert "3 well(s)" in summary
    # Each kept image carries its own bytes, not a neighbour's.
    with zipfile.ZipFile(tmp_path / "dest" / "in" / "A02.zip") as archive:
        assert archive.read(_name("A02", "24h", "f1", "GFP")) == b"A02f1GFP24h"
    # The record describes exactly the images kept, so every reader of it still trusts it.
    record = read_record(tmp_path / "dest")
    assert sorted(record["timepoints"]) == ["24h"]
    assert validate_against_archives(tmp_path / "dest", record).ok


def test_a_typed_schema_splits_names_the_converters_do_not_write(tmp_path):
    def namer(well, tp, field, channel):
        return f"{well}-{tp}-{channel}-{field}.tif"
    src = _raw(tmp_path / "src", record=False, namer=namer)
    plan, _ = _run(src, tmp_path / "dest", SubsetFilter(fovs=frozenset({"f1"})),
                   schema="well:timepoint:channel:fov", separator="-")
    assert plan.raw_source.origin == FROM_TYPED
    assert _members(tmp_path / "dest", "A01") == sorted(
        namer("A01", t, "f1", c) for t in TPS for c in CHANNELS)


def test_a_typed_schema_that_contradicts_the_record_is_refused(tmp_path):
    src = _raw(tmp_path / "src")
    # Fields swapped: the record's field tokens would land in the timepoint slot.
    with pytest.raises(SubsetError, match="doesn't agree with this dataset's acquisition record"):
        build_subset_plan(src, tmp_path / "dest", SubsetFilter(timepoint_labels=frozenset({"24h"})),
                          schema="experiment:channel:well:timepoint:fov")


def test_a_typed_schema_some_names_do_not_fit_is_refused_naming_one(tmp_path):
    src = _raw(tmp_path / "src", record=False)
    with pytest.raises(SubsetError, match="don't fit the filename schema") as err:
        build_subset_plan(src, tmp_path / "dest", SubsetFilter(fovs=frozenset({"f0"})),
                          schema="well:fov:timepoint")
    assert "E_DAPI_" in str(err.value)


def test_without_a_record_a_confident_reading_of_the_names_is_used(tmp_path):
    src = _raw(tmp_path / "src", record=False)
    plan, _ = _run(src, tmp_path / "dest", SubsetFilter(channels=frozenset({"GFP"})))
    assert plan.raw_source.origin == FROM_INFERRED
    assert _members(tmp_path / "dest", "B01") == _expected("B01", channels=("GFP",))


def test_with_no_schema_a_wells_only_subset_still_works_and_any_other_axis_is_refused(tmp_path):
    def namer(well, tp, field, channel):       # nothing the inference can be confident about
        return f"img{field[1:]}{tp[:-1]}{channel[0]}.tif"
    src = _raw(tmp_path / "src", record=False, namer=namer)
    with pytest.raises(SubsetError, match="Type the filename schema"):
        build_subset_plan(src, tmp_path / "dest", SubsetFilter(timepoint_labels=frozenset({"24h"})))
    plan, _ = _run(src, tmp_path / "dest", SubsetFilter(wells=frozenset({"A02"})))
    assert plan.raw_source.origin == ""
    assert sorted(p.name for p in (tmp_path / "dest" / "in").glob("*.zip")) == ["A02.zip"]
    assert len(_members(tmp_path / "dest", "A02")) == len(TPS) * len(FIELDS) * len(CHANNELS)


def test_archives_that_no_longer_match_the_record_are_refused(tmp_path):
    src = _raw(tmp_path / "src")
    with zipfile.ZipFile(src / "in" / "A01.zip", "a") as archive:
        archive.writestr(_name("A01", "72h", "f0", "GFP"), b"x")
    with pytest.raises(SubsetError, match="don't match its acquisition record"):
        build_subset_plan(src, tmp_path / "dest", SubsetFilter(timepoint_labels=frozenset({"24h"})))


def test_a_filter_that_keeps_no_value_the_archives_hold_is_refused_before_writing(tmp_path):
    src = _raw(tmp_path / "src")
    with pytest.raises(SubsetError, match="No channel"):
        build_subset_plan(src, tmp_path / "dest", SubsetFilter(channels=frozenset({"RFP"})))
    assert not (tmp_path / "dest").exists()


# ── what an analysed dataset still refuses ───────────────────────────────────
def test_an_analysed_dataset_refuses_a_channel_filter_and_an_in_place_edit(tmp_path):
    src = _raw(tmp_path / "src")
    (src / "out").mkdir()
    (src / "out" / "pipeline_info.json").write_text(json.dumps({"schema_fields": []}))
    with pytest.raises(SubsetError, match="Choosing channels is only"):
        build_subset_plan(src, tmp_path / "dest", SubsetFilter(channels=frozenset({"GFP"})))
    with pytest.raises(SubsetError, match="Editing in place is only"):
        build_subset_plan(src, "", SubsetFilter(wells=frozenset({"A01"})), in_place=True)


# ── sidecars ─────────────────────────────────────────────────────────────────
def test_anchors_and_the_short_well_record_follow_the_wells_and_timepoints_kept(tmp_path):
    src = _raw(tmp_path / "src")
    write_anchors(anchors_path(src / "in"), {
        ("A01", "0h"): Anchor("A01", "0h", 1.0, 2.0),
        ("A01", "24h"): Anchor("A01", "24h", 3.0, 4.0),
        ("B01", "24h"): Anchor("B01", "24h", 5.0, 6.0),
    })
    write_gap_record(src, {"A01": 2, "B01": 1})
    _run(src, tmp_path / "dest", SubsetFilter(wells=frozenset({"A01", "A02"}),
                                              timepoint_labels=frozenset({"24h", "48h"})))
    assert set(read_anchors(anchors_path(tmp_path / "dest" / "in"))) == {("A01", "24h")}
    assert read_gap_record(tmp_path / "dest") == {"A01": 2}


# ── in place ─────────────────────────────────────────────────────────────────
def test_in_place_removes_a_timepoint_and_a_channel_from_the_sources_own_archives(tmp_path):
    src = _raw(tmp_path / "src")
    (src / DONE_MARKER).write_text("{}")
    untouched = (src / "in" / "A01.zip").stat().st_size
    plan, summary = _run(src, "", SubsetFilter(timepoint_labels=frozenset({"0h", "24h"}),
                                               channels=frozenset({"DAPI"})), in_place=True)
    assert plan.dest.root == plan.src.root
    for well in WELLS:
        assert _members(src, well) == _expected(well, timepoints=("0h", "24h"), channels=("DAPI",))
    assert (src / "in" / "A01.zip").stat().st_size < untouched
    assert "removed 24 image(s)" in summary          # 3 wells x (2 fields x (6 - 2) images)
    record = read_record(src)
    assert sorted(record["timepoints"]) == ["0h", "24h"]
    assert validate_against_archives(src, record).ok
    assert not (src / IN_PLACE_MARKER).exists()
    assert not list((src / "in").glob(f".*{STAGING_SUFFIX}"))
    # The folder no longer holds what the conversion that built it wrote, so it may not be topped up.
    assert not (src / DONE_MARKER).exists()


def test_in_place_deletes_the_wells_left_out_and_keeps_the_rest_byte_for_byte(tmp_path):
    src = _raw(tmp_path / "src")
    (src / DONE_MARKER).write_text("{}")
    write_anchors(anchors_path(src / "in"), {("B01", "0h"): Anchor("B01", "0h", 1.0, 2.0)})
    before = (src / "in" / "A01.zip").read_bytes()
    _run(src, "", SubsetFilter(wells=frozenset({"A01", "A02"})), in_place=True)
    assert sorted(p.name for p in (src / "in").glob("*.zip")) == ["A01.zip", "A02.zip"]
    assert (src / "in" / "A01.zip").read_bytes() == before          # nothing to cut: not rewritten
    assert {t["well"] for t in read_record(src)["tiles"]} == {"A01", "A02"}
    assert not anchors_path(src / "in").exists()                     # the one anchor was B01's
    assert (src / DONE_MARKER).exists()                              # wells only: still that conversion's


def test_in_place_with_nothing_to_remove_is_refused(tmp_path):
    src = _raw(tmp_path / "src")
    with pytest.raises(SubsetError, match="Nothing to remove"):
        build_subset_plan(src, "", SubsetFilter(), in_place=True)


def test_an_interrupted_in_place_edit_finishes_when_run_again_and_blocks_a_copy_meanwhile(tmp_path):
    src = _raw(tmp_path / "src")
    filt = SubsetFilter(timepoint_labels=frozenset({"48h"}))
    plan = build_subset_plan(src, "", filt, in_place=True)
    calls = []

    def cancel_after_one():
        calls.append(1)
        return len(calls) > 1

    with pytest.raises(SubsetError, match="Cancelled"):
        execute_plan(plan, is_cancelled=cancel_after_one, workers=1)
    assert (src / IN_PLACE_MARKER).exists()
    cut = [w for w in WELLS if _members(src, w) == _expected(w, timepoints=("48h",))]
    assert len(cut) == 1                                 # one archive cut, the others as they were

    with pytest.raises(SubsetError, match="interrupted in-place edit"):
        build_subset_plan(src, tmp_path / "dest", SubsetFilter(wells=frozenset({"A01"})))
    with pytest.raises(SubsetError, match="Run that same edit again"):
        build_subset_plan(src, "", SubsetFilter(timepoint_labels=frozenset({"0h"})), in_place=True)

    # The archives no longer match the record; the stored schema carries the resume past that.
    plan = build_subset_plan(src, "", filt, in_place=True)
    assert plan.in_place_resume
    execute_plan(plan)
    for well in WELLS:
        assert _members(src, well) == _expected(well, timepoints=("48h",))
    assert validate_against_archives(src, read_record(src)).ok
    assert not (src / IN_PLACE_MARKER).exists()


def test_a_timepoint_token_that_does_not_read_as_hours_is_still_chosen_by_its_name(tmp_path):
    src = _raw(tmp_path / "src", record=False, timepoints=("pre", "post"))
    _run(src, tmp_path / "dest", SubsetFilter(timepoint_labels=frozenset({"post"})),
         schema="experiment:channel:well:fov:timepoint", separator="_")
    assert _members(tmp_path / "dest", "A01") == _expected("A01", timepoints=("post",))


def test_a_copy_with_no_destination_is_refused_rather_than_written_to_the_working_directory(tmp_path):
    src = _raw(tmp_path / "src")
    with pytest.raises(SubsetError, match="Choose a destination folder"):
        build_subset_plan(src, "", SubsetFilter(wells=frozenset({"A01"})))
