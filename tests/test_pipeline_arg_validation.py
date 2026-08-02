"""Audit E4 — ``build_pipeline_args`` must not silently drop a present-but-
invalid numeric option. Silently omitting a malformed ``--tophat_radius_*`` /
``--mesh_*`` flag ran the pipeline with *different parameters than the operator
configured*; a bad value must now raise instead of vanishing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.pipeline_service import _emit_num_flag, build_pipeline_args

_P, _IN, _OUT = Path("/pipe.py"), Path("/in"), Path("/out")


def _opts(**over):
    o = dict(
        nuclear_token="NIR", fluor_tokens=["GFP"], csv_prefix="m",
        filename_schema="experiment:channel:well:fov:timepoint",
        filename_sep="_", segmentation_method="stardist_nuclei",
        min_nucleus_area_px=50, smfish_tokens=[], shape_features=[],
        tf_threads="0", workers="0", flatfield="none", background_well="",
        qc_remove_debris=False, qc_min_area=0.0, qc_max_area=0.0,
        qc_min_solidity=0.0,
    )
    o.update(over)
    return o


def _flag_value(args, flag):
    return args[args.index(flag) + 1]


def test_valid_int_flag_emitted():
    args = build_pipeline_args(_P, _IN, _OUT, _opts(tophat_radius_nir="100"))
    assert _flag_value(args, "--tophat_radius_nir") == "100"


def test_absent_key_skipped_silently():
    # A missing key means "use the pipeline default" — no flag, no error.
    args = build_pipeline_args(_P, _IN, _OUT, _opts())
    assert "--tophat_radius_nir" not in args


def test_blank_value_skipped():
    args = build_pipeline_args(_P, _IN, _OUT, _opts(tophat_radius_nir="   "))
    assert "--tophat_radius_nir" not in args


def test_invalid_int_raises_not_swallowed():
    # "100.5" is not a valid int — previously silently dropped.
    with pytest.raises(ValueError):
        build_pipeline_args(_P, _IN, _OUT, _opts(tophat_radius_nir="100.5"))


def test_invalid_float_raises_not_swallowed():
    with pytest.raises(ValueError):
        build_pipeline_args(_P, _IN, _OUT, _opts(mesh_ratio="abc"))


def test_emit_num_flag_helper_contract():
    args = []
    _emit_num_flag(args, {"r": "7"}, "r", "--r", int)
    assert args == ["--r", "7"]

    args = []
    _emit_num_flag(args, {}, "r", "--r", int)          # absent → skip
    assert args == []


def test_phase_token_emitted_for_any_method():
    # A phase channel now drives background-correction passthrough for every
    # method, so --phase_token is sent whenever set — not only for label-free.
    args = build_pipeline_args(
        _P, _IN, _OUT,
        _opts(segmentation_method="stardist_seeded_watershed_cell",
              cytoplasm_token="CY", phase_token="BF"),
    )
    assert _flag_value(args, "--phase_token") == "BF"


def test_phase_token_still_emitted_for_labelfree():
    args = build_pipeline_args(
        _P, _IN, _OUT,
        _opts(segmentation_method="cellpose_labelfree", phase_token="BF"),
    )
    assert _flag_value(args, "--phase_token") == "BF"


def test_phase_token_absent_when_unset():
    args = build_pipeline_args(_P, _IN, _OUT, _opts())
    assert "--phase_token" not in args


def test_stardist_tile_px_emitted_when_set():
    args = build_pipeline_args(_P, _IN, _OUT, _opts(stardist_tile_px="2048"))
    assert _flag_value(args, "--stardist_tile_px") == "2048"


def test_stardist_tile_px_off_by_default_or_zero():
    # Unset → no flag (pipeline default = off).
    assert "--stardist_tile_px" not in build_pipeline_args(_P, _IN, _OUT, _opts())
    # Explicit 0 also means off → no flag.
    assert "--stardist_tile_px" not in build_pipeline_args(
        _P, _IN, _OUT, _opts(stardist_tile_px="0"))

    args = []
    _emit_num_flag(args, {"r": ""}, "r", "--r", int)   # blank → skip
    assert args == []

    args = []
    _emit_num_flag(args, {"r": None}, "r", "--r", int)  # None → skip
    assert args == []

    with pytest.raises(ValueError):
        _emit_num_flag([], {"r": "x"}, "r", "--r", int)  # present+invalid → raise


# ── StarDist detection thresholds ────────────────────────────────────────────
# Blank / "auto" / out-of-range must emit nothing, so the run falls back to the
# threshold the pretrained model ships with. Emitting a rejected value instead
# would fail the run at argparse; emitting a silently corrected one would
# segment the plate with a threshold the operator never chose.

def test_stardist_thresholds_emitted_when_set():
    args = build_pipeline_args(_P, _IN, _OUT, _opts(
        stardist_prob_thresh="0.25", stardist_nms_thresh="0.5"))
    assert float(_flag_value(args, "--stardist_prob_thresh")) == 0.25
    assert float(_flag_value(args, "--stardist_nms_thresh")) == 0.5


def test_stardist_thresholds_absent_by_default():
    args = build_pipeline_args(_P, _IN, _OUT, _opts())
    assert "--stardist_prob_thresh" not in args
    assert "--stardist_nms_thresh" not in args


@pytest.mark.parametrize("blank", ["", "   ", "auto", "AUTO", None])
def test_blank_or_auto_means_the_models_own_default(blank):
    args = build_pipeline_args(_P, _IN, _OUT, _opts(stardist_prob_thresh=blank))
    assert "--stardist_prob_thresh" not in args


@pytest.mark.parametrize("bad", ["1.5", "-0.2", "abc"])
def test_out_of_range_threshold_is_not_emitted(bad):
    args = build_pipeline_args(_P, _IN, _OUT, _opts(stardist_prob_thresh=bad))
    assert "--stardist_prob_thresh" not in args


def test_zero_threshold_is_emitted():
    """0 is a real threshold (detect everything), not an absent one."""
    args = build_pipeline_args(_P, _IN, _OUT, _opts(stardist_prob_thresh="0"))
    assert float(_flag_value(args, "--stardist_prob_thresh")) == 0.0


def test_the_pipeline_accepts_what_the_form_emits():
    """The two halves must agree: every flag build_pipeline_args produces has to
    parse on the pipeline side, and the sentinel default has to survive."""
    import process_microscopy as pm

    args = build_pipeline_args(_P, _IN, _OUT, _opts(
        stardist_prob_thresh="0.25", stardist_nms_thresh="0.5"))
    parsed = pm.build_parser().parse_args(
        ["--input_dir", "/in", "--output_dir", "/out"] + args[2:])
    assert pm._stardist.parse_threshold(parsed.stardist_prob_thresh) == 0.25
    assert pm._stardist.parse_threshold(parsed.stardist_nms_thresh) == 0.5

    default = pm.build_parser().parse_args(["--input_dir", "/in", "--output_dir", "/out"])
    assert pm._stardist.parse_threshold(default.stardist_prob_thresh) is None
    assert pm._stardist.parse_threshold(default.stardist_nms_thresh) is None
