"""The pipeline's end-of-run threshold dispatcher (``run_auto_threshold_pass``).

Three modes — skipped, Otsu, control-well — plus the rule the user asked for:
the segmentation channel is written 0, and a value already in the sidecar is
never overwritten.

These drive the dispatcher directly with a stand-in args object and a fake
output dir, so no segmentation, no images, and no pipeline run are involved.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from process_microscopy import _segmentation_tokens, run_auto_threshold_pass


LOG = logging.getLogger("test_auto_threshold_pass")


def _args(**over):
    base = dict(
        no_auto_threshold=False,
        nuclear_token="NIR",
        cytoplasm_token="",
        segmentation_method="stardist_nuclei",
        csv_prefix="measurements",
        filename_schema="experiment:channel:well:fov:timepoint",
        filename_sep="_",
        pos_control_well="",
        neg_control_well="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _control_dataset(tmp_path, *, pos="B02", neg="B11"):
    """Two per-well CSVs with a cleanly separated GFP population."""
    (tmp_path / f"measurements_{pos}.csv").write_text(
        "gfp_mean_intensity\n" + "\n".join("400.0" for _ in range(50)) + "\n")
    (tmp_path / f"measurements_{neg}.csv").write_text(
        "gfp_mean_intensity\n" + "\n".join("100.0" for _ in range(50)) + "\n")
    (tmp_path / "pipeline_info.json").write_text(json.dumps({"schema_version": 2}))


def _thresholds(tmp_path) -> dict:
    info = json.loads((tmp_path / "pipeline_info.json").read_text())
    return info.get("cell_gating", {}).get("thresh_frac_on", {})


# ── segmentation tokens ──────────────────────────────────────────────────────

def test_segmentation_tokens_is_the_nuclear_token():
    assert _segmentation_tokens(_args()) == {"nir"}


def test_segmentation_tokens_adds_cytoplasm_only_for_seeded_watershed():
    seeded = _args(segmentation_method="stardist_seeded_watershed_cell",
                   cytoplasm_token="mCherry")
    assert _segmentation_tokens(seeded) == {"nir", "mcherry"}
    # The same cytoplasm token under a different method is an ordinary channel.
    other = _args(segmentation_method="stardist_nuclei", cytoplasm_token="mCherry")
    assert _segmentation_tokens(other) == {"nir"}


def test_segmentation_tokens_drops_blanks():
    assert _segmentation_tokens(_args(nuclear_token="   ")) == set()


# ── the three modes ──────────────────────────────────────────────────────────

def test_no_auto_threshold_writes_nothing(tmp_path):
    _control_dataset(tmp_path)
    written = run_auto_threshold_pass(
        _args(no_auto_threshold=True, pos_control_well="B02",
              neg_control_well="B11"),
        tmp_path, ["NIR", "GFP"], LOG,
    )
    assert written == {}
    assert _thresholds(tmp_path) == {}


def test_control_wells_drive_the_threshold(tmp_path):
    _control_dataset(tmp_path)
    written = run_auto_threshold_pass(
        _args(pos_control_well="B02", neg_control_well="B11"),
        tmp_path, ["NIR", "GFP"], LOG,
    )
    assert 100.0 <= written["gfp"] < 400.0
    assert _thresholds(tmp_path)["gfp"] == written["gfp"]


def test_the_segmentation_channel_is_written_zero(tmp_path):
    _control_dataset(tmp_path)
    run_auto_threshold_pass(
        _args(pos_control_well="B02", neg_control_well="B11"),
        tmp_path, ["NIR", "GFP"], LOG,
    )
    assert _thresholds(tmp_path)["nir"] == 0.0


def test_the_segmentation_channel_is_zero_without_control_wells(tmp_path):
    """The zero is unconditional — it is not part of the control-well mode.

    With no control wells and no ``*_out.zip`` to Otsu, the estimator returns
    nothing, and the seg channel's 0 must still land.
    """
    _control_dataset(tmp_path)
    run_auto_threshold_pass(_args(), tmp_path, ["NIR", "GFP"], LOG)
    assert _thresholds(tmp_path) == {"nir": 0.0}


def test_a_user_set_value_is_never_overwritten(tmp_path):
    """Including the segmentation channel — "default 0", not "always 0"."""
    _control_dataset(tmp_path)
    (tmp_path / "pipeline_info.json").write_text(json.dumps({
        "schema_version": 2,
        "cell_gating": {"thresh_frac_on": {"nir": 12.0, "gfp": 34.0}},
    }))
    written = run_auto_threshold_pass(
        _args(pos_control_well="B02", neg_control_well="B11"),
        tmp_path, ["NIR", "GFP"], LOG,
    )
    assert written == {}
    assert _thresholds(tmp_path) == {"nir": 12.0, "gfp": 34.0}


def test_the_segmentation_channel_is_excluded_from_the_estimate(tmp_path):
    """It is pinned, so spending the sweep on it would be waste — and a
    control-derived value for it would be silently discarded anyway."""
    _control_dataset(tmp_path)
    (tmp_path / "measurements_B02.csv").write_text(
        "gfp_mean_intensity,nir_mean_intensity\n"
        + "\n".join("400.0,900.0" for _ in range(50)) + "\n")
    (tmp_path / "measurements_B11.csv").write_text(
        "gfp_mean_intensity,nir_mean_intensity\n"
        + "\n".join("100.0,200.0" for _ in range(50)) + "\n")
    run_auto_threshold_pass(
        _args(pos_control_well="B02", neg_control_well="B11"),
        tmp_path, ["NIR", "GFP"], LOG,
    )
    # NIR has a perfectly good separation available and is still 0.
    assert _thresholds(tmp_path)["nir"] == 0.0


def test_half_given_control_wells_fall_back_to_the_otsu_mode(tmp_path):
    """The form blocks this, but the CLI is a supported entry point too."""
    _control_dataset(tmp_path)
    run_auto_threshold_pass(
        _args(pos_control_well="B02"), tmp_path, ["NIR", "GFP"], LOG,
    )
    # Otsu found no *_out.zip, so only the pinned seg channel is written —
    # crucially NOT a control-derived gfp value from a one-sided comparison.
    assert _thresholds(tmp_path) == {"nir": 0.0}


def test_the_pass_never_raises(tmp_path):
    """Auto-threshold is informational; the run's real output is already on
    disk and a failure here must not surface as a run failure."""
    missing = tmp_path / "not_a_dir"
    assert run_auto_threshold_pass(
        _args(pos_control_well="B02", neg_control_well="B11"),
        missing, ["NIR", "GFP"], LOG,
    ) == {}


# ── the batched-remote argv round-trip ───────────────────────────────────────

def test_argv_parse_reads_every_fluor_token():
    """``--fluor_tokens`` is multi-valued; reading it with a single ``next()``
    picked up only the first, so a batched remote run thresholded one channel."""
    from services.remote_runner import _auto_threshold_args_from_argv

    args = _auto_threshold_args_from_argv([
        "process_microscopy.py",
        "--fluor_tokens", "GFP", "mCherry", "DAPI",
        "--nuclear_token", "NIR",
        "--csv_prefix", "measurements",
        "--pos_control_well", "B02",
        "--neg_control_well", "B11",
        "--no_auto_threshold",
    ])
    assert args.fluor_tokens == ["GFP", "mCherry", "DAPI"]
    assert args.nuclear_token == "NIR"
    assert args.pos_control_well == "B02"
    assert args.neg_control_well == "B11"
    # The batching layer sets --no_auto_threshold to suppress the per-batch
    # pass; this local re-run must not honour it or a batched run never gets
    # thresholds at all.
    assert args.no_auto_threshold is False


@pytest.mark.parametrize("argv,expected", [
    ([], "measurements"),
    (["--csv_prefix", "cells"], "cells"),
])
def test_argv_parse_defaults(argv, expected):
    from services.remote_runner import _auto_threshold_args_from_argv
    assert _auto_threshold_args_from_argv(argv).csv_prefix == expected


def test_argv_parse_applies_the_labelfree_phase_remap():
    """The argv is what the GUI built, so it is pre-remap.

    ``process_microscopy.main`` moves ``--phase_token`` onto the nuclear slot for
    a label-free run, and ``_segmentation_tokens`` asks which channel that was.
    Reading the raw ``--nuclear_token`` here named a channel that was never
    segmented, so the pass zeroed the wrong channel's threshold and derived a
    real one from the brightfield image.
    """
    from process_microscopy import _segmentation_tokens
    from services.remote_runner import _auto_threshold_args_from_argv

    argv = [
        "process_microscopy.py",
        "--fluor_tokens", "BF", "GFP",
        "--nuclear_token", "NIR",
        "--phase_token", "BF",
        "--segmentation_method", "cellpose_labelfree",
    ]
    args = _auto_threshold_args_from_argv(argv)
    # This assertion first, and phrased through ``_segmentation_tokens``: it is
    # the question the pass actually asks, and it reads the args object through
    # ``getattr`` defaults, so it measures the old behaviour rather than dying on
    # an attribute the old code never set.
    assert _segmentation_tokens(args) == {"bf"}
    assert args.nuclear_token == "BF"
    assert args.phase_token == "BF"


def test_argv_parse_leaves_the_nuclear_token_alone_for_other_methods():
    from process_microscopy import _segmentation_tokens
    from services.remote_runner import _auto_threshold_args_from_argv

    args = _auto_threshold_args_from_argv([
        "process_microscopy.py",
        "--fluor_tokens", "BF", "GFP",
        "--nuclear_token", "NIR",
        "--phase_token", "BF",
        "--segmentation_method", "stardist_nuclei",
    ])
    assert args.nuclear_token == "NIR"
    assert _segmentation_tokens(args) == {"nir"}


def test_quantified_tokens_lead_with_segmentation_and_do_not_repeat_it():
    """After the label-free remap the segmentation channel is also a member of
    ``--fluor_tokens``; nothing downstream may measure it twice."""
    from util.channel_roles import quantified_channel_tokens as q

    assert q(["BF", "GFP"], nuclear_token="BF") == ["BF", "GFP"]
    assert q(["GFP", "nir"], nuclear_token="NIR") == ["NIR", "GFP"]
    assert q(["GFP", "RFP"], nuclear_token="") == ["GFP", "RFP"]
    # The cytoplasm channel joins the list only under seeded watershed.
    assert q(["GFP"], nuclear_token="DAPI", cytoplasm_token="CY") == ["DAPI", "GFP"]
    assert q(["GFP"], nuclear_token="DAPI", cytoplasm_token="CY",
             segmentation_method="stardist_seeded_watershed_cell") == \
        ["DAPI", "GFP", "CY"]
