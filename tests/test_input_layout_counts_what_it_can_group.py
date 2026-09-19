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


@pytest.mark.parametrize("stem,counted", [
    ("exp_DAPI_A01_f0_t0", True),
    ("exp_DAPI_a1_f0_t0", True),       # the canonical form accepts either spelling
    ("exp_DAPI_ZZ9_f0_t0", False),     # splits right, well token is not a well
    ("scan0", False),                  # too few positions to reach the well slot
])
def test_the_predicate_agrees_with_what_grouping_will_do(stem, counted):
    """Excludes the count built on token agreement alone — ``ZZ9`` splits into
    exactly the five positions the schema wants."""
    assert groupable_tif_count([f"{stem}.tif"], SCHEMA, "_") == int(counted)
