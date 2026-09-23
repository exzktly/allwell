"""Planning, field validation and the resume marker for the CZI converter.

The marker is the high-stakes half. Two mappings of one source write
*identical filenames* from different images, so nothing on disk distinguishes
an ``A01.zip`` made from scene 0 from one made from scene 7. Only the marker
can tell a run that may finish another's work from one that must refuse —
CLAUDE.md §7.7 #3, where the failure mode is a complete-looking plate of
half-wrong numbers. So every field the mapping depends on is mutated alone
here, and each one alone must decline the resume.
"""

from __future__ import annotations

import dataclasses

import pytest

from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi
from datasets.czi_import.czi_import_compute import (
    IN_PROGRESS_MARKER,
    _MAPPING_FIELDS,
    ConversionParams,
    CziImportError,
    RawFields,
    default_dest,
    destination_problem,
    plan_conversion,
    read_source,
    resume_state,
    source_files,
    strip_czi_suffix,
    write_run_marker,
)


@pytest.fixture
def plate(tmp_path):
    return write_czi(
        tmp_path / "Plate7.czi",
        plate_tiles(wells=2, fovs=2, timepoints=2, channels=2),
        xml=plate_metadata_xml(scene_wells={0: "C07", 1: "A02"},
                               channels=["DAPI", "GFP"]),
    )


# ── the plan ─────────────────────────────────────────────────────────────────

def test_the_plan_states_the_mapping_and_where_it_came_from(plate):
    plan = plan_conversion(read_source(plate, ConversionParams()), ConversionParams())

    assert plan.well_labels == ["C07", "A02"]
    assert plan.from_file is True
    assert plan.fovs_per_well == 2
    assert plan.n_timepoints == 2
    assert plan.channels == ["DAPI", "GFP"]
    summary = plan.summary()
    assert "C07→A02" in summary
    assert "named by the file" in summary
    # 2 wells x 2 FOVs x 2 timepoints x 2 channels
    assert "16 image(s)" in summary


def test_a_grid_plan_says_it_came_from_the_grid(tmp_path):
    path = write_czi(tmp_path / "bare.czi",
                     plate_tiles(wells=4, fovs=1, timepoints=1, channels=1))
    params = ConversionParams(well_source="grid", grid_rows=2, grid_cols=2)
    plan = plan_conversion(read_source(path, params), params)

    assert plan.well_labels == ["A01", "A02", "B02", "B01"]
    assert plan.from_file is False
    assert "from the plate grid" in plan.summary()


def test_a_file_short_of_a_plane_still_plans_and_says_so(tmp_path):
    """A gap is no longer a refusal, so a short file plans like any other —
    and the plan carries the sentence that says it will come out short.

    Said here, on the plan, because that is what the page renders *before* the
    run. As a refusal this news could only ever arrive at the end of the read,
    which on a large export is an hour spent to be told no.
    """
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=2)
    del tiles[1]
    path = write_czi(tmp_path / "short.czi", tiles)

    params = ConversionParams(well_source="grid", grid_rows=1, grid_cols=2)
    plan = plan_conversion(read_source(path, params), params)

    assert plan.well_labels == ["A01", "A02"]
    assert "3 of the 4 image(s)" in plan.shortfall_warning
    # Its own sentence, not folded into the one about timepoint names: they
    # are different news and a reader has to be able to tell which is which.
    assert plan.shortfall_warning != plan.timepoint_warning


def test_a_grid_that_does_not_fit_is_still_a_grid_error(tmp_path):
    """What the refusal used to mask. With the file no longer refused
    outright, a grid of three columns for two positions has to be reported as
    the grid problem it is."""
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=2)
    del tiles[1]
    path = write_czi(tmp_path / "short.czi", tiles)

    params = ConversionParams(well_source="grid", grid_rows=1, grid_cols=3)
    with pytest.raises(CziImportError):
        plan_conversion(read_source(path, params), params)


def test_reading_something_that_is_not_a_czi_is_reported_not_raised(tmp_path):
    """The page asks this on every keystroke, so a broken file is a *problem*
    on the set rather than an exception in a readout. ``plan_conversion`` is
    what turns it into a ``CziImportError`` at the point a caller wants one."""
    path = tmp_path / "nope.czi"
    path.write_bytes(b"II*\0" + b"\0" * 200)
    params = ConversionParams()

    czi_set = read_source(path, params)
    assert "not a CZI file" in czi_set.problem
    with pytest.raises(CziImportError, match="not a CZI file"):
        plan_conversion(czi_set, params)


# ── the fields as typed ──────────────────────────────────────────────────────

def test_a_blank_numeric_field_is_not_an_error(plate):
    """Blank means "take it from the file", which is a valid answer. Treating
    it as a zero tells the user their grid is invalid before they have finished
    typing it."""
    assert RawFields().problems() == []
    assert RawFields().to_params().fovs_per_well is None


@pytest.mark.parametrize("fields,fragment", [
    (RawFields(well_source="grid"), "imaged well-grid rows and columns"),
    (RawFields(well_source="grid", rows="two", cols="2"), "positive whole numbers"),
    (RawFields(fovs="0"), "FOVs per well"),
    (RawFields(workers="-1"), "Worker threads"),
    (RawFields(well_source="nonsense"), "Unknown well source"),
])
def test_a_field_that_cannot_be_used_says_which_one(fields, fragment):
    problems = fields.problems()
    assert any(fragment in p for p in problems), problems


def test_the_typed_fields_become_the_params_the_run_uses(plate):
    params = RawFields(well_source="grid", rows="2", cols="3", fovs="4",
                       origin="b3", workers="6", direction="col",
                       serpentine=False, experiment=" Run9 ").to_params()

    assert params == ConversionParams(
        well_source="grid", grid_rows=2, grid_cols=3, origin="b3",
        direction="col", serpentine=False, fovs_per_well=4,
        experiment="Run9", workers=6, use_folder=False)


@pytest.mark.parametrize("name,expected", [
    ("Plate7.czi", "Plate7"),
    ("Plate7.CZI", "Plate7"),
    ("Plate7", "Plate7"),
    ("a.b.czi", "a.b"),
])
def test_the_czi_suffix_is_stripped_for_the_experiment_token(name, expected):
    assert strip_czi_suffix(name) == expected


def test_the_destination_defaults_to_a_folder_beside_the_source(plate):
    assert default_dest(plate) == plate.parent / "Plate7"


# ── the resume marker ────────────────────────────────────────────────────────

def test_a_folder_holding_this_run_s_own_interrupted_work_is_resumable(
        plate, tmp_path):
    dest = tmp_path / "dest"
    params = ConversionParams()
    write_run_marker(dest, plate, params)
    (dest / "in").mkdir(parents=True)
    (dest / "in" / "C07.zip").write_bytes(b"")

    state = resume_state(dest, plate, params)

    assert state.resumable is True
    assert state.wells_done == 1
    assert state.mismatched is False
    assert destination_problem(dest, plate, params) == ""


@pytest.mark.parametrize("field", _MAPPING_FIELDS)
def test_every_declared_mapping_field_alone_declines_the_resume(
        field, plate, tmp_path):
    """Mutated one at a time, because a key that is declared but not compared
    is worse than one that is missing: it reads as covered."""
    dest = tmp_path / "dest"
    params = ConversionParams()
    write_run_marker(dest, plate, params)
    (dest / "in").mkdir(parents=True)
    (dest / "in" / "C07.zip").write_bytes(b"")

    changed = {
        "well_source": "grid", "grid_rows": 4, "grid_cols": 6,
        "origin": "B03", "direction": "col", "serpentine": False,
        "fovs_per_well": 3, "experiment": "Other", "use_folder": True,
        "timepoint_stride": 3,
    }[field]
    other = dataclasses.replace(params, **{field: changed})

    state = resume_state(dest, plate, other)
    assert state.mismatched is True, f"{field} does not decline a resume"
    assert state.resumable is False
    assert "different settings" in destination_problem(dest, plate, other)


def test_the_worker_count_is_not_part_of_the_mapping(plate, tmp_path):
    """It changes how fast the conversion goes and nothing about what it
    writes, so refusing a resume over it would refuse one that was safe."""
    assert "workers" not in _MAPPING_FIELDS
    dest = tmp_path / "dest"
    params = ConversionParams()
    write_run_marker(dest, plate, params)

    assert resume_state(dest, plate,
                        dataclasses.replace(params, workers=7)).resumable


def test_a_source_replaced_at_the_same_path_is_a_different_source(
        plate, tmp_path):
    """A re-export, a restore, the next plate saved over the last. Resuming
    across that leaves half of one plate and half of another under one set of
    filenames."""
    dest = tmp_path / "dest"
    params = ConversionParams()
    write_run_marker(dest, plate, params)
    (dest / "in").mkdir(parents=True)
    (dest / "in" / "C07.zip").write_bytes(b"")

    write_czi(plate, plate_tiles(wells=2, fovs=1, timepoints=1, channels=1),
              xml=plate_metadata_xml(scene_wells={0: "C07", 1: "A02"},
                                     channels=["DAPI"]))

    assert resume_state(dest, plate, params).mismatched is True


def test_a_marker_with_no_wells_behind_it_is_not_a_collision(plate, tmp_path):
    """A run that failed before its first archive, or was cancelled and then
    had its settings changed. There is nothing on disk for a second mapping to
    contaminate, and the marker is a hidden file that "empty the folder" does
    not clear."""
    dest = tmp_path / "dest"
    write_run_marker(dest, plate, ConversionParams())
    other = ConversionParams(well_source="grid", grid_rows=2, grid_cols=2)

    assert resume_state(dest, plate, other) == type(
        resume_state(dest, plate, other))()
    assert destination_problem(dest, plate, other) == ""


def test_a_destination_holding_someone_else_s_plate_is_refused(plate, tmp_path):
    dest = tmp_path / "dest"
    (dest / "in").mkdir(parents=True)
    (dest / "in" / "A01.zip").write_bytes(b"")

    assert "Already holds converted wells" in destination_problem(
        dest, plate, ConversionParams())


def test_a_finished_dataset_is_refused_by_its_pipeline_info(plate, tmp_path):
    dest = tmp_path / "dest"
    (dest / "out").mkdir(parents=True)
    (dest / "out" / "pipeline_info.json").write_text("{}")

    assert "Already holds a finished dataset" in destination_problem(
        dest, plate, ConversionParams())


def test_the_two_converters_do_not_share_a_marker_filename(plate):
    """A folder carrying the OME converter's leftovers is not this tool's run
    to finish, and the two write the same filenames from different sources."""
    from datasets.ome_import.ome_import_compute import (
        IN_PROGRESS_MARKER as OME_MARKER,
    )

    assert IN_PROGRESS_MARKER == ".aw_czi_in_progress"
    assert IN_PROGRESS_MARKER != OME_MARKER


# ── split-scene sets ─────────────────────────────────────────────────────────

def _split_plate(tmp_path, wells=("C07", "A02", "H11")):
    folder = tmp_path / "Exp7_split"
    folder.mkdir(parents=True, exist_ok=True)
    for well in wells:
        write_czi(folder / f"Exp7_{well}.czi",
                  plate_tiles(wells=1, fovs=1, timepoints=1, channels=2),
                  xml=plate_metadata_xml(scene_wells={0: well},
                                         channels=["DAPI", "GFP"]))
    return folder


def test_a_lone_file_converts_itself_unless_the_folder_is_asked_for(tmp_path):
    """The tick-box is the whole difference between a one-well dataset and a
    three-well one from the same selection — which is why it keys the resume."""
    folder = _split_plate(tmp_path)
    picked = folder / "Exp7_A02.czi"

    alone = ConversionParams()
    together = ConversionParams(use_folder=True)

    assert source_files(picked, alone) == [picked]
    assert [p.name for p in source_files(picked, together)] == [
        "Exp7_A02.czi", "Exp7_C07.czi", "Exp7_H11.czi"]
    assert plan_conversion(read_source(picked, alone), alone).well_labels == ["A02"]
    assert plan_conversion(read_source(picked, together),
                           together).well_labels == ["A02", "C07", "H11"]


def test_a_folder_is_the_set_whatever_use_folder_says(tmp_path):
    """The box only ever widens a lone *file*; a folder already is the plate,
    so an unticked box must not silently convert one scene of it."""
    folder = _split_plate(tmp_path)
    for params in (ConversionParams(), ConversionParams(use_folder=True)):
        assert len(source_files(folder, params)) == 3


def test_the_plan_says_how_many_files_it_reads(tmp_path):
    """Ninety-six wells from one file and from ninety-six are two very
    different things to check before pressing Convert."""
    folder = _split_plate(tmp_path)
    params = ConversionParams(use_folder=True)
    plan = plan_conversion(read_source(folder, params), params)

    assert plan.n_files == 3
    assert "across 3 file(s)" in plan.summary()


def test_a_set_writes_beside_its_folder_and_is_named_for_it(tmp_path):
    """Not for whichever member sorted first, which would bury a whole plate
    under one scene's name — and **not the source folder itself**, which is
    what "beside it, named after it" degenerates to for a folder and would
    have written ``in/`` in among the .czi files the plate was read from.
    Asserted against the source path, not just against a name."""
    folder = _split_plate(tmp_path)
    picked = folder / "Exp7_A02.czi"

    for dest in (default_dest(folder),
                 default_dest(picked, ConversionParams(use_folder=True))):
        assert dest == tmp_path / "Exp7_split_dataset"
        assert dest != folder
        assert folder not in dest.parents and dest != folder
    # A single file still drops its suffix, which is already beside itself.
    assert default_dest(picked, ConversionParams()) == folder / "Exp7_A02"


def test_a_sibling_appearing_between_runs_declines_the_resume(tmp_path):
    """The shared marker fingerprints the *selected* path, which is identical
    either way; only the set's own fingerprint sees a fourth file arrive, and
    resuming across it stitches one folder out of two acquisitions."""
    folder = _split_plate(tmp_path)
    picked = folder / "Exp7_A02.czi"
    params = ConversionParams(use_folder=True)
    dest = tmp_path / "dest"
    write_run_marker(dest, picked, params)
    (dest / "in").mkdir(parents=True)
    (dest / "in" / "A02.zip").write_bytes(b"")
    assert resume_state(dest, picked, params).resumable is True

    write_czi(folder / "Exp7_D04.czi",
              plate_tiles(wells=1, fovs=1, timepoints=1, channels=2),
              xml=plate_metadata_xml(scene_wells={0: "D04"},
                                     channels=["DAPI", "GFP"]))

    assert resume_state(dest, picked, params).mismatched is True
