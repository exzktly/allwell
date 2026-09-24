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


# ── the schema need not name every slot ──────────────────────────────────────
#
# Reported: "calculate cell gating by default is checked in analyze, but does
# not run automatically". The pass ran; it estimated nothing. The dataset's
# schema was ``well:channel:timepoint:ignore:ignore`` (files like
# ``A01_w2Turq_T04.tif``) — no ``fov`` slot — and the member scan required a
# non-empty fov token, so every ``*_labels`` and ``*_tophat`` member in every
# archive was dropped. Each channel then reported "only 0 sample(s)", which
# reads as "this channel had no data", and only the segmentation channel's
# unconditional 0.0 reached the sidecar.

def _out_zip(path, *, stems, labels_stem_of):
    """An ``*_out.zip`` holding a (labels, tophat) pair per stem.

    *stems* are the input stems the pipeline would have processed; the writer
    names output members ``<input stem><suffix><ext>``, which is what the
    estimator parses back with the run's filename schema.
    """
    import io
    import zipfile

    import numpy as np
    import tifffile

    def _tif(arr):
        buf = io.BytesIO()
        tifffile.imwrite(buf, arr)
        return buf.getvalue()

    labels = np.zeros((16, 16), dtype=np.uint16)
    for i in range(4):
        labels[i * 4:i * 4 + 3, i * 4:i * 4 + 3] = i + 1
    with zipfile.ZipFile(path, "w") as zf:
        written_labels = set()
        for stem in stems:
            lab = labels_stem_of(stem)
            if lab not in written_labels:
                zf.writestr(f"{lab}_labels.tif", _tif(labels))
                written_labels.add(lab)
            # A graded intensity per nucleus so Otsu has a real split to find.
            fluor = (labels.astype(np.float32) * 500.0)
            zf.writestr(f"{stem}_tophat.tif", _tif(fluor))


def _estimate(tmp_path, *, schema, sep="_", channels, stems, labels_stem_of):
    from pipeline.auto_threshold import estimate_thresholds_from_outputs

    for well in ("A01", "A02"):
        _out_zip(tmp_path / f"{well}_out.zip",
                 stems=[s.format(well=well) for s in stems],
                 labels_stem_of=labels_stem_of)
    return estimate_thresholds_from_outputs(
        tmp_path, fluor_channels=list(channels), filename_schema=schema,
        filename_sep=sep, log=LOG)


def test_schema_without_an_fov_slot_still_estimates(tmp_path):
    # The reported dataset, in its reported shape.
    got = _estimate(
        tmp_path,
        schema="well:channel:timepoint:ignore:ignore",
        channels=["dic", "w3cit"],
        stems=["{well}_dic_T01", "{well}_w3cit_T01",
               "{well}_dic_T04", "{well}_w3cit_T04"],
        labels_stem_of=lambda s: s.replace("_dic_", "_w1594_")
                                  .replace("_w3cit_", "_w1594_"),
    )
    assert sorted(got) == ["dic", "w3cit"]
    assert all(v > 0 for v in got.values()), got


def test_schema_without_a_timepoint_slot_still_estimates(tmp_path):
    got = _estimate(
        tmp_path,
        schema="well:channel:fov",
        channels=["gfp"],
        stems=["{well}_gfp_f0", "{well}_gfp_f1"],
        labels_stem_of=lambda s: s.replace("_gfp_", "_dapi_"),
    )
    assert sorted(got) == ["gfp"]


def test_schema_with_neither_fov_nor_timepoint_still_estimates(tmp_path):
    got = _estimate(
        tmp_path,
        schema="well:channel",
        channels=["gfp"],
        stems=["{well}_gfp"],
        labels_stem_of=lambda s: s.replace("_gfp", "_dapi"),
    )
    assert sorted(got) == ["gfp"]


def test_a_named_fov_slot_left_empty_is_still_rejected(tmp_path):
    """The other half of the rule, and the reason it is not simply "default the
    fov to 0": a schema that *declares* fov describes a dataset whose fields are
    genuinely distinct, so a stem too short to carry one is malformed and
    collapsing it to a constant would merge different fields into one sample."""
    got = _estimate(
        tmp_path,
        schema="experiment:channel:well:fov:timepoint",
        channels=["gfp"],
        stems=["exp_gfp_{well}"],            # three tokens, no fov, no timepoint
        labels_stem_of=lambda s: s.replace("_gfp_", "_dapi_"),
    )
    assert got == {}


def test_an_unplaceable_archive_says_so(tmp_path, caplog):
    """A configuration mismatch and an empty well used to look identical.

    Every member carrying a role suffix but none of them placeable by the run's
    schema is the mismatch; it now names the schema and an example member
    instead of leaving each channel to report "only 0 sample(s)".
    """
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        got = _estimate(
            tmp_path,
            schema="experiment:channel:well:fov:timepoint",
            channels=["gfp"],
            stems=["exp_gfp_{well}"],
            labels_stem_of=lambda s: s.replace("_gfp_", "_dapi_"),
        )
    assert got == {}
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("filename schema" in m and "experiment:channel:well:fov:timepoint" in m
               for m in msgs), msgs


def test_a_placeable_archive_does_not_warn(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        got = _estimate(
            tmp_path,
            schema="well:channel:timepoint:ignore:ignore",
            channels=["w3cit"],
            stems=["{well}_w3cit_T01", "{well}_w3cit_T04"],
            labels_stem_of=lambda s: s.replace("_w3cit_", "_w1594_"),
        )
    assert sorted(got) == ["w3cit"]
    assert not [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING], caplog.records


# ── "nothing computed" and "nothing new" are different outcomes ──────────────

def test_estimating_nothing_at_all_warns(tmp_path, caplog):
    """The reported run logged "produced no new defaults", which reads as "this
    was already done". Nothing had been computed for either readout channel —
    the opposite situation, and the only one that is a problem."""
    (tmp_path / "pipeline_info.json").write_text(json.dumps({}))
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        run_auto_threshold_pass(
            _args(nuclear_token="w1594",
                  filename_schema="well:channel:timepoint:ignore:ignore"),
            tmp_path, ["w1594", "dic", "w3cit"], LOG)
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("no threshold could be estimated" in m and "DIC" in m for m in msgs), msgs


def test_everything_already_stored_is_not_a_warning(tmp_path, caplog):
    """The genuinely-nothing-to-do case: a value WAS estimated for every readout
    channel, and every one of them was already in the sidecar."""
    _control_dataset(tmp_path)
    (tmp_path / "pipeline_info.json").write_text(json.dumps(
        {"cell_gating": {"thresh_frac_on": {"nir": 0.0, "gfp": 123.0}}}))
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        written = run_auto_threshold_pass(
            _args(pos_control_well="B02", neg_control_well="B11"),
            tmp_path, ["NIR", "GFP"], LOG)
    assert written == {}
    assert not [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING], caplog.records


def test_the_seg_channels_zero_does_not_mask_an_empty_estimate(tmp_path, caplog):
    """The reported run's shape exactly: the seg channel's unconditional 0.0 is
    a successful write, so the pass logged "defaults written for 1 channel(s)"
    over a run that had estimated nothing for either readout channel."""
    (tmp_path / "pipeline_info.json").write_text(json.dumps({}))
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        written = run_auto_threshold_pass(
            _args(nuclear_token="w1594"), tmp_path, ["w1594", "dic"], LOG)
    assert written == {"w1594": 0.0}          # the write still happens…
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("no threshold could be estimated" in m for m in msgs), msgs


# ── batched remote: the checkbox must survive the batcher's own suppression ──

def test_batched_remote_honours_an_unchecked_box():
    """``--no_auto_threshold`` reaches the remote argv for two unrelated
    reasons — the user unchecking the box, and the batcher suppressing the
    per-batch pass so a sliver of the plate cannot write plate-wide defaults.
    Once appended they look identical, and the local end-of-run re-run treated
    every batched run as "the batcher did it", writing thresholds for operators
    who had explicitly asked for none."""
    import services.remote_runner as rr

    p = object.__new__(rr._BatchedRemoteProcess)

    p._pipeline_argv = ["--csv_prefix", "measurements"]
    assert p._auto_threshold_off_by_user() is False

    p._pipeline_argv = ["--csv_prefix", "measurements", "--no_auto_threshold"]
    assert p._auto_threshold_off_by_user() is True


def _batched_that_will_skip_every_well(tmp_path, argv):
    """A ``_BatchedRemoteProcess`` whose plate is already complete locally.

    Every well has its per-well CSV, so ``_run_batches`` skips them all, sets
    returncode 0 and reaches the end-of-run auto-threshold decision without any
    ssh. Built with ``object.__new__`` for the same reason the batched-progress
    tests do: the real ``__init__`` needs ssh, staging and a Fetcher.
    """
    import queue
    import threading
    import types

    import services.remote_runner as rr

    p = object.__new__(rr._BatchedRemoteProcess)
    zip_dir = tmp_path / "in"
    zip_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    zips = []
    for well in ("A01", "A02"):
        z = zip_dir / f"{well}.zip"
        z.write_bytes(b"PK")
        zips.append(z)
        (out_dir / f"measurements_{well}.csv").write_text("cell_id\n1\n")
    p._zipped = types.SimpleNamespace(well_zips=zips, incremental=False)
    p._local_output_dir = out_dir
    p._pipeline_argv = argv
    p._batch_size = 2
    p._line_q = queue.Queue()
    p._log = lambda _m: None
    p._returncode = None
    p._stop = threading.Event()
    p._suspending = threading.Event()
    p._fetcher = types.SimpleNamespace(
        run=types.SimpleNamespace(well_count_total=0), flush_and_join=lambda: None)
    calls = []
    p._run_local_auto_threshold = lambda: calls.append(1)
    return p, calls


def test_the_batched_local_rerun_runs_when_the_box_was_checked(tmp_path):
    proc, calls = _batched_that_will_skip_every_well(
        tmp_path, ["--csv_prefix", "measurements"])
    proc._run_batches()
    assert proc._returncode == 0
    assert calls == [1]


def test_the_batched_local_rerun_is_skipped_when_the_box_was_unchecked(tmp_path):
    """The whole point: asserting the outbound call, not the predicate. The
    predicate was always computable — nothing consulted it, so an operator who
    unchecked the box still got thresholds written."""
    proc, calls = _batched_that_will_skip_every_well(
        tmp_path, ["--csv_prefix", "measurements", "--no_auto_threshold"])
    proc._run_batches()
    assert proc._returncode == 0
    assert calls == []
