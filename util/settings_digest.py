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

**What is deliberately outside it.** The input and output paths (the well's own
identity already carries those), ``--force`` and ``--incremental`` (re-processing
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

#: Every option whose value changes the numbers a run produces, and why it does.
#: Anything absent from an ``opts`` mapping is treated as unset rather than as a
#: default, so adding a key here does not invalidate a digest recorded before it
#: existed — the recorded string is compared whole, and a run that did not know
#: about a key simply never wrote it.
KEYED_ON: dict[str, str] = {
    # ── what is segmented, and how ──────────────────────────────────────────
    "segmentation_method":   "a different segmenter finds different cells",
    "nuclear_token":         "names the channel segmentation runs on",
    "cytoplasm_token":       "the second channel seeded watershed grows into",
    "phase_token":           "label-free segmentation's input channel",
    "min_nucleus_area_px":   "drops nuclei below it, changing the cell set",
    "stardist_prob_thresh":  "how confident StarDist must be to call a nucleus",
    "stardist_nms_thresh":   "how much overlap merges two detections",
    "cellpose_diameter":     "the scale Cellpose looks for cells at",
    "cellpose_flow_thresh":  "rejects cells whose flow error exceeds it",
    "cellpose_cellprob":     "the probability floor for calling a pixel a cell",
    # ── what is measured ────────────────────────────────────────────────────
    "fluor_tokens":          "the channels quantified into the CSV columns",
    "smfish_tokens":         "which channels get spot counts rather than means",
    "shape_features":        "adds or removes whole CSV columns",
    "qc_options":            "the debris gate; changes which cells are kept",
    # ── how the pixels are corrected before measurement ─────────────────────
    "background":            "the background model subtracted from every frame",
    "tophat_radius":         "the structuring element the top-hat uses",
    "fft_radius":            "the band the FFT filter keeps",
    "flatfield":             "divides every frame by an illumination field",
    "empty_well":            "builds that correction from a blank well",
    "background_well":       "which blank the correction is built from",
    # ── how a filename is read ──────────────────────────────────────────────
    "filename_schema":       "decides which token is the well, fov, timepoint",
    "filename_sep":          "the same, and a wrong one groups nothing",
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
