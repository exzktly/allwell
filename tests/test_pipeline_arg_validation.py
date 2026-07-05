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

    args = []
    _emit_num_flag(args, {"r": ""}, "r", "--r", int)   # blank → skip
    assert args == []

    args = []
    _emit_num_flag(args, {"r": None}, "r", "--r", int)  # None → skip
    assert args == []

    with pytest.raises(ValueError):
        _emit_num_flag([], {"r": "x"}, "r", "--r", int)  # present+invalid → raise
