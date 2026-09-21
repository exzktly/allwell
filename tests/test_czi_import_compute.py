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
    read_layout,
    resume_state,
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
    plan = plan_conversion(read_layout(plate), ConversionParams())

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
    plan = plan_conversion(read_layout(path),
                           ConversionParams(well_source="grid", grid_rows=2,
                                            grid_cols=2))

    assert plan.well_labels == ["A01", "A02", "B02", "B01"]
    assert plan.from_file is False
    assert "from the plate grid" in plan.summary()


def test_an_unsound_file_is_reported_as_unsound_not_as_a_bad_grid(tmp_path):
    """No grid can make an incomplete file convertible, so the readout must
    say *that* rather than a divisibility error derived from it."""
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=2)
    del tiles[1]
    path = write_czi(tmp_path / "short.czi", tiles)

    with pytest.raises(CziImportError, match="incomplete"):
        plan_conversion(read_layout(path),
                        ConversionParams(well_source="grid", grid_rows=1,
                                         grid_cols=3))


def test_reading_something_that_is_not_a_czi_is_a_tool_error(tmp_path):
    path = tmp_path / "nope.czi"
    path.write_bytes(b"II*\0" + b"\0" * 200)
    with pytest.raises(CziImportError, match="not a CZI file"):
        read_layout(path)


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
        experiment="Run9", workers=6)


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
        "fovs_per_well": 3, "experiment": "Other",
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
