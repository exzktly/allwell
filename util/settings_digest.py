"""One short string naming the configuration that produced a well's output.

The pipeline has two "we already did this" predicates —
:func:`pipeline.well_skip.output_exists_for_well`, which asks whether a well's
CSV and archive both exist, and the incremental done-set, which asks which
``(fov, timepoint)`` pairs are already covered. Neither was keyed on anything
about *how* the run was configured, so re-running a plate with a different
segmentation method, a different threshold or a different channel set was
answered "up to date" and the old numbers kept. Worse, ``write_pipeline_info``
then rewrote the sidecar to advertise the **new** configuration, so the plate
described a setup that had produced none of the numbers in it.

Measured on the unfixed code: a plate segmented with ``stardist_nuclei`` at
prob 0.2, re-run as ``cellpose`` with ``min_nucleus_area_px=200`` at prob 0.9,
logged "up to date (no new FOVs)" for every well, kept run 1's CSV rows, and
published a sidecar naming cellpose. Exit 0, ``run_check`` complete.

**What belongs in the key.** Only what can change the numbers in a CSV or the
pixels in an output archive. Two runs whose digests match must be producing
interchangeable output; two that differ must not be assumed to. ``KEYED_ON``
below is that roster, with the reason each entry is in it, and
``tests/test_reuse_is_keyed_completely.py`` mutates every one of them
individually and requires the reuse to decline.

**What is deliberately outside it.** ``--focus_min`` is the interesting one,
because it looks like it belongs here and does not: the run *records* that
number in the sidecar and never acts on it, so two runs differing only in it
produce byte-identical CSVs. Keying on it would make changing a threshold cost
a full re-segmentation of the plate, which is the opposite of the reason it is
recorded rather than applied. ``--no_focus_metric`` **is** keyed, because it
decides whether the column exists at all. Also outside: the input and output
paths (the well's own identity already carries those), ``--force`` and ``--incremental`` (re-processing
knobs, not a change of configuration — that is what lets a forced re-run land in
the same place and overwrite it), worker counts and other execution knobs that
change only how long the run takes, and anything about the machine: this digest
has to be identical for the same plate computed locally and on a worker host.

This module lives in ``util/`` because ``pipeline/`` must not import
``services/`` and the existing argv digest
(``services.remote_run_id.pipeline_argv_digest``) drags ``services.remote_config``
in with it. It is stdlib-only for the same reason it is in ``util/``: the remote
manifest uploads every ``util/`` file to the worker host.
"""
from __future__ import annotations

import hashlib
import json

#: Every pipeline option whose value changes the numbers a run produces, and
#: why it does. The keys are ``process_microscopy.build_parser()`` destinations,
#: because the one caller digests that parser's namespace — a key spelled any
#: other way silently contributes nothing, which is how the first version of
#: this roster came to name six options that do not exist.
#: ``tests/test_reuse_is_keyed_on_the_settings.py`` reads the real parser and
#: fails on a key it does not carry.
#:
#: Anything absent from an ``opts`` mapping is treated as unset rather than as a
#: default, so adding a key here does not invalidate a digest recorded before it
#: existed — the recorded string is compared whole, and a run that did not know
#: about a key simply never wrote it.
KEYED_ON: dict[str, str] = {
    # ── what is segmented, and how ──────────────────────────────────────────
    "segmentation_method":         "a different segmenter finds different cells",
    "nuclear_token":               "names the channel segmentation runs on",
    "cytoplasm_token":             "the second channel seeded watershed grows into",
    "phase_token":                 "label-free segmentation's input channel",
    "min_nucleus_area_px":         "drops nuclei below it, changing the cell set",
    "stardist_prob_thresh":        "how confident StarDist must be to call a nucleus",
    "stardist_nms_thresh":         "how much overlap merges two detections",
    "stardist_tile_px":            "tiling changes what is detected at the seams",
    "cellpose_model":              "a different weight set finds different cells",
    "cellpose_diameter":           "the scale Cellpose looks for cells at",
    "cellpose_flow_threshold":     "rejects cells whose flow error exceeds it",
    "cellpose_cellprob_threshold": "the probability floor for calling a pixel a cell",
    "cellpose_nuclei_channel":     "the channel handed to Cellpose as the nucleus",
    # ── what is measured ────────────────────────────────────────────────────
    "fluor_tokens":                "the channels quantified into the CSV columns",
    "smfish_tokens":               "which channels get spot counts rather than means",
    "shape_features":              "adds or removes whole CSV columns",
    "no_focus_metric":             "drops the per-cell focus column from every row",
    # ── which cells are kept ────────────────────────────────────────────────
    "qc_remove_debris":            "turns the debris gate on and off",
    "qc_min_area":                 "the gate's lower area bound",
    "qc_max_area":                 "the gate's upper area bound",
    "qc_min_solidity":             "the gate's shape bound",
    # ── how the pixels are corrected before measurement ─────────────────────
    "background":                  "the background model subtracted from every frame",
    "tophat_radius_nir":           "the structuring element the nuclear top-hat uses",
    "tophat_radius_fluor":         "the same, for every quantified channel",
    "no_tophat_nir":               "skips the nuclear top-hat entirely",
    "no_tophat_fluor":             "skips it for the quantified channels",
    "fft_small_nir":               "the band the nuclear FFT filter keeps",
    "fft_small_fluor":             "the same, for the quantified channels",
    "mesh_ratio":                  "the mesh background model's grid spacing",
    "mesh_sigma":                  "the mesh background model's smoothing",
    "flatfield":                   "divides every frame by an illumination field",
    "background_dir":              "the blank the correction is built from",
    "background_well":             "which well inside it that blank is",
    "background_well_map":         "which blank corrects which well, so a well "
                                   "re-paired to a different blank is corrected "
                                   "against different pixels",
    # ── what is written into the output archive ─────────────────────────────
    "no_save_tophat":              "decides whether corrected images are archived",
    # ── the gate the plate is scored against ────────────────────────────────
    "no_auto_threshold":           "turns off the on/off gate the CSV columns use",
    "pos_control_well":            "the positive control the gate is fitted to",
    "neg_control_well":            "the negative control the gate is fitted to",
    # ── how a filename is read ──────────────────────────────────────────────
    "filename_schema":             "decides which token is the well, fov, timepoint",
    "filename_sep":                "the same, and a wrong one groups nothing",
}

#: Every other parser destination, with the reason it is *not* in the key. This
#: exists so that adding an option to the pipeline is a decision rather than an
#: omission: the liveness test requires ``KEYED_ON | NOT_KEYED_ON`` to be the
#: whole parser, so a new option fails the suite until someone has said which
#: side of the line it falls on.
NOT_KEYED_ON: dict[str, str] = {
    # Identity, not configuration — the well's own output path already carries
    # these, and this digest has to be computable for a plate wherever it runs.
    "input_dir":             "where the images came from",
    "output_dir":            "where the results go",
    "scratch_dir":           "where intermediates are staged",
    "csv_prefix":            "renames the CSV, so a change already misses the "
                             "existing-output check by filename",
    # Re-processing knobs. Keying on these is what would stop a forced re-run
    # landing in the same place and overwriting what is there.
    "force":                 "asks for a re-run; it does not change the result",
    "force_wells":           "the same, for named wells",
    "incremental":           "asks for reuse; it does not change the result",
    "incremental_prior_dir": "where reuse looks, not what is computed",
    # Execution and machine. The digest must be identical for the same plate
    # computed locally and on a worker host.
    "workers":               "how many wells run at once",
    "tf_threads":            "TensorFlow's thread count",
    "cpu_only":              "which device runs the segmenter",
    "allow_low_disk":        "a pre-flight refusal, not a computation",
    "no_run_check":          "skips the end-of-run audit; computes nothing",
    # Recorded rather than applied. The run writes this number into the sidecar
    # as the dataset's stated cut and never acts on it, so two runs differing
    # only here produce byte-identical CSVs -- and keying on it would make
    # changing a threshold cost a full re-segmentation, which is the opposite
    # of the reason it is recorded rather than applied. ``no_focus_metric``,
    # which decides whether the column exists at all, IS keyed.
    "focus_min":             "recorded in the sidecar; the run never acts on it",
}


def settings_digest(opts) -> str:
    """A stable short hash of the options in :data:`KEYED_ON` that *opts* sets.

    Absent keys are omitted rather than defaulted, so the digest depends only on
    what the run actually stated. Values are rendered through ``json.dumps`` with
    sorted keys, so a list's order still counts — ``fluor_tokens`` of
    ``["GFP", "RFP"]`` produces different CSV columns from ``["RFP", "GFP"]`` —
    while a dict's key order does not.

    Returns ``""`` when *opts* sets none of them, which a caller must read as
    "unknown", never as "matches".
    """
    payload = {}
    for key in sorted(KEYED_ON):
        try:
            value = opts[key]
        except (KeyError, TypeError):
            continue
        if value is None:
            continue
        try:
            payload[key] = json.dumps(value, sort_keys=True, default=str)
        except (TypeError, ValueError):
            payload[key] = repr(value)
    if not payload:
        return ""
    blob = "|".join(f"{k}={payload[k]}" for k in sorted(payload))
    return hashlib.blake2b(blob.encode(), digest_size=8).hexdigest()


def settings_match(recorded: str, current: str) -> bool:
    """Whether a well recorded under *recorded* may be reused by *current*.

    An empty *recorded* means the well predates this record, and is accepted:
    grandfathering, so introducing the check does not force every user to
    re-process every plate they touch. The cost is that a plate written before
    this existed keeps the old behaviour until its next forced run, which is
    stated in the docs rather than left for someone to discover.

    An empty *current* means this run could not describe itself, and is also
    accepted — refusing there would make every caller that cannot supply opts
    re-run the world.
    """
    if not recorded or not current:
        return True
    return recorded == current
