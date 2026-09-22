"""Planning, field validation and the resume marker for the ND2 converter.

The marker is the high-stakes half. Two mappings of one source write
*identical filenames* from different images, so nothing on disk distinguishes
an ``A01.zip`` made from position 0 from one made from position 7. Only the
marker can tell a run that may finish another's work from one that must refuse
— CLAUDE.md §7.7 #3, where the failure mode is a complete-looking plate of
half-wrong numbers. So every field the mapping depends on is mutated alone
here, and each one alone must decline the resume.
"""

from __future__ import annotations

import dataclasses

import pytest

from tests._nd2_fixture import write_nd2

pytest.importorskip("nd2")

from datasets.nd2_import.nd2_import_compute import (  # noqa: E402
    IN_PROGRESS_MARKER,
    _MAPPING_FIELDS,
    ConversionParams,
    Nd2ImportError,
    RawFields,
    default_dest,
    destination_problem,
    plan_conversion,
    read_source,
    resume_state,
    strip_nd2_suffix,
    write_run_marker,
)


@pytest.fixture
def plate(tmp_path):
    """Four positions naming two wells — two fields of view each."""
    return write_nd2(tmp_path / "Plate7.nd2", n_pos=4, n_t=2, n_c=2,
                     well_names=["C07", "C07", "A02", "A02"],
                     channel_names=["DAPI", "GFP"])


@pytest.fixture
def bare(tmp_path):
    """Four positions naming nothing — what the grid fallback is for."""
    return write_nd2(tmp_path / "bare.nd2", n_pos=4, n_t=1, n_c=1,
                     well_names=["", "", "", ""])


# ── the plan ─────────────────────────────────────────────────────────────────

def test_the_plan_states_the_mapping_and_where_it_came_from(plate):
    params = ConversionParams()
    plan = plan_conversion(read_source(plate, params), params)

    assert plan.well_labels == ["C07", "A02"]      # acquisition order
    assert plan.fovs_per_well == 2
    assert plan.n_timepoints == 2
    assert plan.channels == ["DAPI", "GFP"]
    assert plan.from_file is True
    assert "named by the file" in plan.summary()
    assert "C07→A02" in plan.summary()
    # 2 wells × 2 FOVs × 2 timepoints × 2 channels
    assert "16 image(s)" in plan.summary()


def test_a_grid_plan_says_it_came_from_the_grid(bare):
    params = ConversionParams(well_source="grid", grid_rows=2, grid_cols=2)
    plan = plan_conversion(read_source(bare, params), params)

    assert plan.well_labels == ["A01", "A02", "B02", "B01"]   # 2×2 serpentine
    assert plan.from_file is False
    assert "from the plate grid" in plan.summary()


def test_an_unnamed_plate_on_the_file_source_points_at_the_grid(bare):
    params = ConversionParams()
    with pytest.raises(Nd2ImportError) as caught:
        plan_conversion(read_source(bare, params), params)

    assert "Switch the well source to the plate grid" in str(caught.value)


def test_reading_something_that_is_not_an_nd2_is_reported_not_raised(tmp_path):
    """The page calls this on every keystroke, so a traceback in a readout
    helps nobody — the refusal belongs at ``plan_conversion``, which is where
    a caller is entitled to one."""
    broken = tmp_path / "broken.nd2"
    broken.write_bytes(b"\xda\xce\xbe\x0a" + b"\0" * 200)

    layout = read_source(broken, ConversionParams())

    assert layout.n_positions == 0
    assert layout.warnings
    with pytest.raises(Nd2ImportError):
        plan_conversion(layout, ConversionParams())


# ── the typed fields ─────────────────────────────────────────────────────────

def test_a_blank_numeric_field_is_not_an_error():
    """"Not filled in yet" and "filled in wrongly" are different answers; a
    page that conflates them tells the user their grid is invalid before they
    have finished typing it."""
    assert RawFields(well_source="file").problems() == []


@pytest.mark.parametrize("fields,fragment", [
    (RawFields(well_source="grid"), "Enter the imaged well-grid"),
    (RawFields(well_source="grid", rows="two", cols="2"), "positive whole"),
    (RawFields(fovs="0"), "FOVs per well"),
    (RawFields(workers="-1"), "Worker threads"),
    (RawFields(timepoint_stride="x"), "Keep every Nth timepoint"),
    (RawFields(well_source="scenes"), "Unknown well source"),
])
def test_a_field_that_cannot_be_used_says_which_one(fields, fragment):
    assert any(fragment in problem for problem in fields.problems())


def test_the_typed_fields_become_the_params_the_run_uses():
    params = RawFields(
        well_source="grid", rows="2", cols="3", fovs="4", origin="b03",
        workers="5", timepoint_stride="6", direction="col", serpentine=False,
        experiment="  Run9  ",
    ).to_params()

    assert params == ConversionParams(
        well_source="grid", grid_rows=2, grid_cols=3, origin="b03",
        direction="col", serpentine=False, fovs_per_well=4,
        timepoint_stride=6, experiment="Run9", workers=5)


@pytest.mark.parametrize("name,expected", [
    ("Plate7.nd2", "Plate7"),
    ("Plate7.ND2", "Plate7"),
    ("Plate7", "Plate7"),
    ("a.b.nd2", "a.b"),
])
def test_the_nd2_suffix_is_stripped_for_the_experiment_token(name, expected):
    assert strip_nd2_suffix(name) == expected


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
        "fovs_per_well": 3, "experiment": "Other", "timepoint_stride": 3,
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

    write_nd2(plate, n_pos=2, n_t=1, n_c=1, well_names=["C07", "A02"])

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


def test_no_two_converters_share_a_marker_filename(plate):
    """A folder carrying another converter's leftovers is not this tool's run
    to finish, and they write the same filenames from different sources."""
    from datasets.czi_import.czi_import_compute import (
        IN_PROGRESS_MARKER as CZI_MARKER,
    )
    from datasets.ome_import.ome_import_compute import (
        IN_PROGRESS_MARKER as OME_MARKER,
    )

    assert IN_PROGRESS_MARKER == ".aw_nd2_in_progress"
    assert len({IN_PROGRESS_MARKER, CZI_MARKER, OME_MARKER}) == 3


# ── timepoints ───────────────────────────────────────────────────────────────

def test_a_timelapse_that_records_its_times_is_named_by_elapsed_hours(tmp_path):
    src = write_nd2(tmp_path / "Time.nd2", n_pos=2, n_t=3, n_c=1,
                    well_names=["C07", "A02"],
                    times_ms=[0.0, 3_600_000.0, 7_200_000.0])
    params = ConversionParams()
    plan = plan_conversion(read_source(src, params), params)

    assert plan.timepoint_tokens == ["0h", "1h", "2h"]
    assert plan.timepoint_warning == ""


def test_a_timelapse_with_no_times_says_what_was_lost_and_why(tmp_path):
    """Degraded output must never be indistinguishable from good output
    (CLAUDE.md §7): a plate named by position has no time axis in Review, and
    the moment to find that out is while the source can still be re-acquired.
    """
    src = write_nd2(tmp_path / "Untimed.nd2", n_pos=2, n_t=3, n_c=1,
                    well_names=["C07", "A02"], times_ms=None)
    params = ConversionParams()
    plan = plan_conversion(read_source(src, params), params)

    assert plan.timepoint_tokens == ["t0", "t1", "t2"]
    assert plan.timepoint_warning == (
        "Timepoints are named by position (t0, t1, …) rather than by elapsed "
        "time — the file records no acquisition time for its frames.")


def test_a_stride_keeps_the_kept_timepoints_own_names(tmp_path):
    """Never renumbered: t0 and t2 stay two hours apart in Review rather than
    being compressed into neighbours."""
    src = write_nd2(tmp_path / "Time.nd2", n_pos=2, n_t=5, n_c=1,
                    well_names=["C07", "A02"],
                    times_ms=[i * 3_600_000.0 for i in range(5)])
    params = ConversionParams(timepoint_stride=2)
    plan = plan_conversion(read_source(src, params), params)

    assert plan.timepoint_tokens == ["0h", "2h", "4h"]
    assert plan.n_timepoints == 3
    assert plan.n_source_timepoints == 5
    # The plain count would be a half-truth: it reads the same as a plate that
    # only ever had three timepoints.
    assert plan.timepoint_phrase == "3 of 5 timepoint(s) (0h, 2h, 4h)"


def test_a_stride_of_one_is_the_whole_timelapse_and_says_nothing_extra(tmp_path):
    src = write_nd2(tmp_path / "Time.nd2", n_pos=2, n_t=3, n_c=1,
                    well_names=["C07", "A02"],
                    times_ms=[0.0, 3_600_000.0, 7_200_000.0])
    params = ConversionParams(timepoint_stride=1)
    plan = plan_conversion(read_source(src, params), params)

    assert plan.timepoint_phrase == "3 timepoint(s)"
