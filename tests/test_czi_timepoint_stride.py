"""Converting every N-th timepoint instead of all of them.

Two things make this feature easy to get wrong in ways a weak test would not
notice, and both shape what is asserted here (CLAUDE.md §7.8).

The first is **desynchronisation**: the stride has to filter the source
indices the planes are read from *and* the tokens those planes are named with.
Filter one without the other and every archive is complete, correctly sized,
and holds the wrong image under each name. Nothing raises. The fixture's planes
each encode their own coordinates, so the tests below read the pixels back and
compare them against the timepoint the member name claims — a check a member
listing alone cannot make.

The second is **renumbering**. Keeping timepoints 0, 3, 6, 9 and naming them
``t0, t1, t2, t3`` would look tidy and would state a spacing the acquisition
never had: ``util.timepoint.parse_hours`` reads ``t3`` as three hours, so the
renamed set tells Review the plate was imaged four times an hour apart. Every
token assertion here is against the source's own numbering for that reason.
"""

from __future__ import annotations

import zipfile

import numpy as np
import pytest

from pipeline.czi_explode import explode_czi
from pipeline.explode_common import stride_note, timepoint_plan
from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi


# ── the pure selector ────────────────────────────────────────────────────────

def test_a_stride_of_one_keeps_every_timepoint():
    tokens = [f"t{i}" for i in range(5)]
    assert timepoint_plan(tokens, 1) == list(enumerate(tokens))


def test_a_stride_keeps_the_source_indices_and_the_source_tokens():
    """Not ``t0..t3``: renumbering would restate the spacing (see the module
    docstring). Twelve points at stride three is the case where the renumbered
    answer and the right one differ at every position but the first."""
    plan = timepoint_plan([f"t{i}" for i in range(12)], 3)
    assert plan == [(0, "t0"), (3, "t3"), (6, "t6"), (9, "t9")]


def test_a_stride_always_keeps_the_first_timepoint():
    """The pre-treatment reading every later point is normalised against. A
    selector counting from the end would keep t2, t5, t8, t11 instead."""
    for stride in range(1, 8):
        assert timepoint_plan([f"t{i}" for i in range(11)], stride)[0] == (0, "t0")


def test_a_stride_larger_than_the_timelapse_keeps_only_the_first():
    assert timepoint_plan(["t0", "t1", "t2"], 10) == [(0, "t0")]


def test_a_single_timepoint_survives_any_stride():
    assert timepoint_plan(["t0"], 4) == [(0, "t0")]


@pytest.mark.parametrize("stride", [0, -1, -7])
def test_a_stride_below_one_is_refused_by_name(stride):
    with pytest.raises(ValueError, match="stride must be 1 or more"):
        timepoint_plan(["t0", "t1"], stride)


def test_the_log_note_names_the_stride_the_total_and_the_kept_tokens():
    """A count alone reads the same as a plate that only had four timepoints."""
    note = stride_note(3, 12, ["t0", "t3", "t6", "t9"])
    assert note == " (every 3rd of 12: t0, t3, t6, t9)"


def test_the_log_note_is_empty_when_nothing_was_dropped():
    assert stride_note(1, 12, [f"t{i}" for i in range(12)]) == ""
    assert stride_note(4, 1, ["t0"]) == ""


# ── the conversion ───────────────────────────────────────────────────────────

def _plate(tmp_path, timepoints):
    """Two named wells, one channel, *timepoints* points.

    ``plate_tiles`` fills each plane with a value derived from all five of its
    coordinates, which is what lets the assertions below tell "read t4, named
    t4" from "read t2, named t4".
    """
    tiles = plate_tiles(wells=2, fovs=1, timepoints=timepoints, channels=1)
    return write_czi(
        tmp_path / "Plate.czi", tiles,
        xml=plate_metadata_xml(scene_wells={0: "C07", 1: "A02"},
                               channels=["DAPI"]))


def _expected_value(scene, fov, t, channel, z=0):
    """The value ``plate_tiles`` writes into the plane at these coordinates."""
    return (((scene * 10 + fov) * 10 + t) * 10 + channel) * 10 + z


def test_a_stride_writes_only_the_kept_timepoints(tmp_path):
    src = _plate(tmp_path, timepoints=6)
    out = tmp_path / "out"
    result = explode_czi(src, out, experiment="Exp", timepoint_stride=2)

    assert result.timepoints == ["t0", "t2", "t4"]
    names = sorted(zipfile.ZipFile(out / "C07.zip").namelist())
    assert names == ["Exp_DAPI_C07_f0_t0.tif", "Exp_DAPI_C07_f0_t2.tif",
                     "Exp_DAPI_C07_f0_t4.tif"]
    assert result.images_written == 2 * 1 * 3 * 1


def test_each_kept_member_holds_the_plane_its_name_claims(tmp_path):
    """The desync check. A stride that filtered the tokens but read
    ``range(size_t)`` writes t0's plane into t0, t1's into t2 and t2's into t4
    — three members, all present, two of them the wrong image."""
    import tifffile

    src = _plate(tmp_path, timepoints=6)
    out = tmp_path / "out"
    explode_czi(src, out, experiment="Exp", timepoint_stride=2)

    with zipfile.ZipFile(out / "C07.zip") as archive:
        for t in (0, 2, 4):
            with archive.open(f"Exp_DAPI_C07_f0_t{t}.tif") as fh:
                plane = tifffile.imread(fh)
            assert int(np.unique(plane)[0]) == _expected_value(0, 0, t, 0), (
                f"t{t} holds the plane from another timepoint")


def test_a_stride_of_one_converts_exactly_what_no_stride_does(tmp_path):
    src = _plate(tmp_path, timepoints=4)
    plain = explode_czi(src, tmp_path / "a", experiment="Exp")
    strided = explode_czi(src, tmp_path / "b", experiment="Exp",
                          timepoint_stride=1)
    assert plain.timepoints == strided.timepoints == ["t0", "t1", "t2", "t3"]
    assert (sorted(zipfile.ZipFile(tmp_path / "a" / "C07.zip").namelist())
            == sorted(zipfile.ZipFile(tmp_path / "b" / "C07.zip").namelist()))


def test_the_run_log_says_which_timepoints_a_stride_kept(tmp_path):
    """Otherwise the only trace of a two-thirds-smaller dataset is a count
    nobody has a reason to be checking."""
    src = _plate(tmp_path, timepoints=6)
    lines: list[str] = []
    explode_czi(src, tmp_path / "out", experiment="Exp", timepoint_stride=3,
                log_fn=lines.append)
    opening = next(line for line in lines if "scene(s) ->" in line)
    assert "2 timepoint(s) (every 3rd of 6: t0, t3)" in opening


def test_a_bad_stride_is_refused_before_anything_is_written(tmp_path):
    src = _plate(tmp_path, timepoints=4)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="stride must be 1 or more"):
        explode_czi(src, out, experiment="Exp", timepoint_stride=0)
    assert not (out / "C07.zip").exists()


def test_resuming_a_strided_run_leaves_its_own_wells_alone(tmp_path):
    """The archive a stride writes must satisfy that stride's own completeness
    check — otherwise every resumed run re-converts the whole plate."""
    src = _plate(tmp_path, timepoints=6)
    out = tmp_path / "out"
    explode_czi(src, out, experiment="Exp", timepoint_stride=2)
    again = explode_czi(src, out, experiment="Exp", timepoint_stride=2,
                        resume=True)
    assert sorted(again.wells_resumed) == ["A02", "C07"]
    assert again.images_written == 0


# ── the readout ──────────────────────────────────────────────────────────────

def _plan_for(tmp_path, timepoints, stride):
    from datasets.czi_import.czi_import_compute import (
        ConversionParams, plan_conversion, read_source,
    )
    src = _plate(tmp_path, timepoints=timepoints)
    params = ConversionParams(timepoint_stride=stride)
    return plan_conversion(read_source(src, params), params)


def test_the_plan_counts_the_timepoints_a_run_would_write(tmp_path):
    """``n_timepoints`` is the kept count. Reporting the source's would make
    the readout's image total describe a run three times the size of the one
    the Convert button starts."""
    plan = _plan_for(tmp_path, timepoints=12, stride=3)
    assert plan.n_timepoints == 4
    assert plan.n_source_timepoints == 12
    assert plan.timepoint_tokens == ["t0", "t3", "t6", "t9"]


def test_the_readout_names_both_counts_and_the_kept_tokens(tmp_path):
    """A bare "4 timepoint(s)" is indistinguishable from a plate that only
    ever had four, which is the whole failure mode a stride introduces."""
    plan = _plan_for(tmp_path, timepoints=12, stride=3)
    assert plan.timepoint_phrase == "4 of 12 timepoint(s) (t0, t3, t6, t9)"
    assert "4 of 12 timepoint(s) (t0, t3, t6, t9)" in plan.summary()


def test_the_readout_says_nothing_extra_when_every_timepoint_is_kept(tmp_path):
    plan = _plan_for(tmp_path, timepoints=12, stride=1)
    assert plan.timepoint_phrase == "12 timepoint(s)"


def test_the_readouts_image_total_counts_only_the_kept_timepoints(tmp_path):
    """2 wells x 1 FOV x 4 of 12 timepoints x 1 channel."""
    plan = _plan_for(tmp_path, timepoints=12, stride=3)
    assert "— 8 image(s)." in plan.summary()


def test_progress_is_reported_against_the_kept_timepoint_count(tmp_path):
    """A total taken from the source's timepoints leaves the bar stuck at a
    third full on a run that finished."""
    src = _plate(tmp_path, timepoints=6)
    seen: list[tuple[int, int]] = []
    explode_czi(src, tmp_path / "out", experiment="Exp", timepoint_stride=3,
                progress_fn=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (4, 4), f"ended at {seen[-1]}"


# ── the wiring, end to end ───────────────────────────────────────────────────

def test_convert_passes_the_stride_through_to_the_archives(tmp_path):
    """``datasets.czi_import.apply.convert`` is the seam a queued job and the
    page both go through. A stride the page collects and ``convert`` drops
    reaches nothing (CLAUDE.md §7.4 #1: assert at the boundary)."""
    from datasets.czi_import.apply import convert
    from datasets.czi_import.czi_import_compute import ConversionParams

    src = _plate(tmp_path, timepoints=6)
    dest = tmp_path / "dest"
    convert(src, dest, ConversionParams(experiment="Exp", timepoint_stride=3))

    names = sorted(zipfile.ZipFile(dest / "in" / "C07.zip").namelist())
    assert names == ["Exp_DAPI_C07_f0_t0.tif", "Exp_DAPI_C07_f0_t3.tif"]
