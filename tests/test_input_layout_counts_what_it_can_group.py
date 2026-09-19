"""The layout readout counts files the grouping can actually reach.

``plan_input_layout``'s docstring already states this rule and the reason for
it, for the *fold-loose* branch: a glob counts every file with a TIF suffix
while the work rejects a name whose well token will not parse, so "Will fold 3
loose TIF(s)" was measured against a run that folded one. The zip-root branch
kept the glob, and the gap only became visible when something else on screen
reported the same folder honestly — a schema detection that refused, sitting
directly above a check mark promising to group eight files it could not group.
"""

from __future__ import annotations

import pytest

from services.input_resolution_service import (
    groupable_tif_count, plan_input_layout,
)

SCHEMA = "experiment:channel:well:fov:timepoint"


def _write(folder, stems):
    folder.mkdir(parents=True, exist_ok=True)
    for stem in stems:
        (folder / f"{stem}.tif").write_bytes(b"img")
    return folder


def _plate(n_wells=3):
    return [f"exp_{c}_{w}_f0_t0"
            for w in [f"A{i:02d}" for i in range(1, n_wells + 1)]
            for c in ("DAPI", "GFP")]


def test_a_folder_none_of_whose_names_carry_a_well_gets_no_check_mark(tmp_path):
    """Excludes the readout that ticks a promise it cannot keep.

    This is the one that costs credibility: a user told in the sidebar that no
    layout could be read, and then shown a white check mark promising to group
    eight files by well, learns that the two statements come from different
    parts of the program and that neither can be relied on.
    """
    folder = _write(tmp_path / "junk", [f"scan{i}" for i in range(8)])
    plan = plan_input_layout(folder, filename_schema=SCHEMA, filename_sep="_")

    assert "✓" not in plan.message
    assert "None of the 8" in plan.message
    assert "nothing would be grouped" in plan.message


def test_a_partial_folder_states_both_numbers(tmp_path):
    """Excludes the readout that counts every file with a TIF suffix."""
    stems = _plate() + ["exp_GFP_ZZ9_f0_t0"]
    folder = _write(tmp_path / "plate", stems)
    plan = plan_input_layout(folder, filename_schema=SCHEMA, filename_sep="_")

    assert f"{len(stems) - 1} of {len(stems)}" in plan.message
    assert "1 does not match the schema" in plan.message


def test_a_clean_folder_reads_exactly_as_it_always_did(tmp_path):
    """The unchanged case must stay unchanged — a count that is right does not
    acquire a qualifier."""
    stems = _plate()
    folder = _write(tmp_path / "clean", stems)
    plan = plan_input_layout(folder, filename_schema=SCHEMA, filename_sep="_")

    assert plan.message == f"✓ Will group {len(stems)} loose TIF file(s) by well → in/"


def test_with_no_schema_the_count_is_the_glob_and_says_nothing_more(tmp_path):
    """Two internal callers want the layout and not the count, and pass no
    schema. Asked without one, the question cannot be answered — and an
    unanswerable question must not come back as a qualifier the reader would
    take as measured."""
    stems = _plate() + ["exp_GFP_ZZ9_f0_t0"]
    folder = _write(tmp_path / "plate", stems)
    plan = plan_input_layout(folder)

    assert plan.message == f"✓ Will group {len(stems)} loose TIF file(s) by well → in/"


# ── the predicate itself ────────────────────────────────────────────────────

def test_the_count_is_none_rather_than_zero_when_it_cannot_be_asked():
    """Undefined is not zero (CLAUDE.md §7 #3). A schema with no well field
    cannot say how many files carry a well, and zero is a measurement."""
    files = ["exp_DAPI_A01_f0_t0.tif"]
    assert groupable_tif_count(files, "", "_") is None
    assert groupable_tif_count(files, "experiment:channel:fov:timepoint", "_") is None
    assert groupable_tif_count(files, SCHEMA, "_") == 1


@pytest.mark.parametrize("name", [
    "exp_DAPI_A01_f0_t0.tif",
    "exp_DAPI_a1_f0_t0.tif",          # the canonical form accepts either spelling
    "exp_DAPI_A01_f0_t0.TIF",
    "exp_DAPI_A01_f0_t0.tiff",
    "exp_DAPI_A01_f0_t1.ome.tif",     # double extension: the stem keeps ".ome"
    "exp_DAPI_ZZ9_f0_t0.tif",         # splits right, well token is not a well
    "exp_DAPI__f0_t0.tif",            # empty token where the well should be
    "scan0.tif",                      # too few positions to reach the well slot
    "exp_DAPI_A01.tif",               # schema longer than the name
])
@pytest.mark.parametrize("schema", [
    SCHEMA,
    "experiment:channel:fov:timepoint",   # no well field at all
    "well:channel",                       # well first
])
def test_the_count_is_a_differential_against_the_zipper_itself(name, schema):
    """Excludes any count with a schema normalisation of its own.

    The docstring claims this mirrors
    ``pipeline.well_plate_zipper._extract_well_from_filename``. A test that
    checks the mirror against hand-written expectations checks the author's
    reading of the zipper, not the zipper — and the first version did exactly
    that, while the implementation disagreed with the real function on an
    uppercase ``WELL`` token and a space-padded one. So ask both.
    """
    from pipeline.well_plate_zipper import _extract_well_from_filename
    from util.schema import parse_schema

    fields = parse_schema(schema)
    expected = int(_extract_well_from_filename(name, fields, "_") is not None)
    assert groupable_tif_count([name], schema, "_") in (expected, None)
    if "well" in fields:
        assert groupable_tif_count([name], schema, "_") == expected


def test_an_unaskable_question_is_refused_rather_than_normalised():
    """Excludes the version that invents a schema spelling or a separator.

    A third spelling of "which position holds the well" is a third answer
    waiting to disagree with ``parse_schema`` and with the zipper. ``WELL`` and
    ``" well"`` are not the well field to either of them, and an empty
    separator makes the zipper raise rather than return an answer.
    """
    name = ["exp_DAPI_A01_f0_t0.tif"]
    assert groupable_tif_count(name, "experiment:channel:WELL:fov:timepoint", "_") is None
    assert groupable_tif_count(name, "experiment:channel: well:fov:timepoint", "_") is None
    assert groupable_tif_count(name, SCHEMA, "") is None


def test_the_named_in_folder_branch_counts_the_same_way(tmp_path):
    """Excludes the half-swept sweep (CLAUDE.md §7.5 #5).

    A folder *named* ``in`` takes the GROUP_LOOSE branch, not the zip-root one
    — the common layout — and it kept the glob after its sibling was converted,
    so one function held two definitions of the same word.
    """
    folder = _write(tmp_path / "in", [f"scan{i}" for i in range(8)])
    plan = plan_input_layout(folder, filename_schema=SCHEMA, filename_sep="_")

    assert "✓" not in plan.message
    assert "None of the 8" in plan.message


def test_the_named_in_folder_branch_states_a_partial_count(tmp_path):
    stems = _plate() + ["exp_GFP_ZZ9_f0_t0"]
    folder = _write(tmp_path / "in", stems)
    plan = plan_input_layout(folder, filename_schema=SCHEMA, filename_sep="_")

    assert f"{len(stems) - 1} of {len(stems)}" in plan.message
    assert "1 does not match the schema" in plan.message
