"""Three predicates that were asked questions outside their domain.

* ``util.schema_infer`` classifies a filename slot as ``fov`` only when it
  reads as ``f<n>``, deliberately — a bare number also reads as a timepoint or
  an experiment index, and letting the FOV predicate claim one is the same
  out-of-domain judgement that once attributed every image to well ``F01``. The
  consequence was that a plate written ``exp_gfp_A01_3_t2`` left the FOV slot
  as ``ignore`` and every field of view in a well merged into one. The slot is
  now claimed in a last pass, where it is decidable: leftover, numeric, more
  than one distinct value, and only once the timepoint slot has been taken.

* ``util.schema.validate_schema`` demanded ``channel`` *and* ``well`` of every
  caller. ``WellPlateZipper`` groups files by well and never reads the channel
  slot — its own ``--filename_schema`` help says so — so a valid ``well:fov``
  was rejected with an error naming a field the tool does not use.

* ``well_viewer.data.auto_threshold`` rebuilt the persisted gating block
  without telling ``build_gating_block`` which channel is nuclear. The nuclear
  channel's ThreshFracOn default is not the one every other channel gets, so a
  nuclear cutoff sitting at the ordinary readout default read as "unchanged"
  and was dropped on write.
"""

from __future__ import annotations

import json

import pytest

from util.schema import PIPELINE_REQUIRED_FIELDS, parse_schema, validate_schema  # noqa: E402
from util.schema_infer import infer_schema  # noqa: E402


# ── the bare-number FOV slot ────────────────────────────────────────────────

def test_a_bare_number_slot_is_claimed_as_fov_when_the_timepoint_is_known():
    stems = ["exp_gfp_A01_3_t2", "exp_gfp_A01_4_t2",
             "exp_rfp_A02_3_t4", "exp_rfp_A02_4_t4"]
    assert infer_schema(stems).schema_str == \
        "experiment:channel:well:fov:timepoint"


def test_an_explicit_fov_slot_still_wins():
    stems = ["exp_gfp_A01_f3_t2", "exp_gfp_A01_f4_t2",
             "exp_rfp_A02_f3_t4", "exp_rfp_A02_f4_t4"]
    assert infer_schema(stems).schema_str == \
        "experiment:channel:well:fov:timepoint"


def test_a_lone_numeric_slot_stays_ignore_when_it_could_be_the_timepoint():
    """No timepoint slot found, so the leftover number is genuinely ambiguous."""
    stems = ["exp_gfp_A01_3", "exp_gfp_A01_4", "exp_rfp_A02_3", "exp_rfp_A02_4"]
    assert infer_schema(stems).schema_str == "experiment:channel:well:ignore"


# ── the zipper's own required fields ────────────────────────────────────────

def test_the_pipeline_still_requires_both_fields():
    assert validate_schema(parse_schema("well:fov")) == [
        '"channel" must appear exactly once in the schema (found 0).'
    ]
    assert PIPELINE_REQUIRED_FIELDS == ("channel", "well")


def test_a_caller_that_reads_only_the_well_can_say_so():
    assert validate_schema(parse_schema("well:fov"), required=("well",)) == []
    assert validate_schema(parse_schema("well:well"), required=("well",))


def test_the_zipper_validates_against_well_alone():
    import ast
    import inspect
    import textwrap

    import WellPlateZipper

    src = textwrap.dedent(inspect.getsource(WellPlateZipper.main))
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "validate_schema"]
    assert len(calls) == 1
    kw = {k.arg for k in calls[0].keywords}
    assert "required" in kw, (
        "the zipper is back to the pipeline's required pair, so a well:fov "
        "schema is rejected over a field it never reads"
    )


def test_the_zipper_and_plate_grid_agree_on_the_plate():
    from util.plate_grid import PLATE_MAX_COL, PLATE_ROWS
    import WellPlateZipper

    wells = WellPlateZipper.generate_wells()
    assert len(wells) == len(PLATE_ROWS) * PLATE_MAX_COL
    assert wells[0] == "A01" and wells[-1] == "H12"


# ── the nuclear token reaching the gating block ─────────────────────────────

def test_rebuilding_the_gating_block_keeps_a_nuclear_cutoff_at_the_readout_default(tmp_path):
    from well_viewer.domain.gating_state import (
        DEFAULT_THRESH_FRAC_ON, build_gating_block, read_nuclear_token,
    )

    (tmp_path / "pipeline_info.json").write_text(json.dumps({
        "nuclear_token": "dapi",
        "cell_gating": {"thresh_frac_on": {"dapi": DEFAULT_THRESH_FRAC_ON}},
    }))

    assert read_nuclear_token(tmp_path) == "dapi"

    without = build_gating_block({}, {"dapi": DEFAULT_THRESH_FRAC_ON})
    assert "thresh_frac_on" not in without, (
        "without the token the nuclear cutoff diffs as default and is dropped"
    )

    with_token = build_gating_block(
        {}, {"dapi": DEFAULT_THRESH_FRAC_ON},
        seg_channel_token=read_nuclear_token(tmp_path),
    )
    assert with_token["thresh_frac_on"] == {"dapi": DEFAULT_THRESH_FRAC_ON}


def test_the_auto_threshold_rebuild_passes_the_token():
    import ast
    import inspect
    import textwrap

    from well_viewer.data import auto_threshold

    src = textwrap.dedent(
        inspect.getsource(auto_threshold.apply_auto_thresholds_to_pipeline_info))
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "build_gating_block"]
    assert len(calls) == 1
    assert "seg_channel_token" in {k.arg for k in calls[0].keywords}, (
        "ARCHITECTURE 5.7 names this as one of three sites that must know "
        "which channel is nuclear before diffing against defaults"
    )


def test_read_nuclear_token_is_quiet_about_a_missing_sidecar(tmp_path):
    from well_viewer.domain.gating_state import read_nuclear_token
    assert read_nuclear_token(tmp_path) == ""


@pytest.mark.parametrize("name,expected", [
    ("._x.tif", True), (".DS_Store", False), ("x.tif", True), (".tif", False),
])
def test_is_group_image_answers_only_about_the_suffix(name, expected):
    """The docstring claimed an AppleDouble sidecar was rejected here; it is not."""
    from util.image_exts import is_group_image
    assert is_group_image(name) is expected
