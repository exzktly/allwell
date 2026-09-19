"""The post-run auto-threshold pass: estimate, then persist.

Extracted from ``process_microscopy`` (which re-exports every public name, so
call sites are unchanged) because it is a self-contained cluster with one job
and the module it came from is under a line budget that only moves down.

The split against its two neighbours is by *layer*, not by subject.
``pipeline.auto_threshold`` samples pixels and runs Otsu, and
``pipeline.threshold_optimize`` reads two control wells' CSVs — both are pure
statistics over what a run wrote. This module is the **pass**: it reads the
run's arguments to decide which of those two applies, decides which channels
are readouts rather than segmentation, merges the result into
``pipeline_info.json`` without overwriting a value the user set, and says in
the log what it did and did not manage. None of that is a threshold
calculation, and all of it is about one run.

Qt-free and ``review``-free, like everything else under ``pipeline/``, so a
headless run produces thresholds on a machine where the GUI is not installed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pipeline.auto_threshold import estimate_thresholds_from_outputs
from pipeline.threshold_optimize import estimate_thresholds_from_controls
from util.json_write import write_json_atomic


def _apply_thresholds_to_pipeline_info(
    output_dir: Path,
    thresholds: dict[str, float],
    log: logging.Logger,
) -> dict[str, float]:
    """Merge ``thresholds`` into ``output_dir/pipeline_info.json`` under
    ``cell_gating.thresh_frac_on``. User-set values are preserved.
    """
    if not thresholds:
        return {}
    info_path = output_dir / "pipeline_info.json"
    if not info_path.exists():
        info_path = output_dir.parent / "pipeline_info.json"
    if not info_path.exists():
        log.info("Auto-threshold: pipeline_info.json not found — defaults not persisted.")
        return {}
    try:
        existing = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Auto-threshold: cannot read %s: %s", info_path, exc)
        return {}
    if not isinstance(existing, dict):
        return {}

    cell_gating = dict(existing.get("cell_gating") or {})
    tfo = dict(cell_gating.get("thresh_frac_on") or {})
    written: dict[str, float] = {}
    for ch, thr in thresholds.items():
        if ch in tfo:  # never overwrite a user-set value
            continue
        tfo[ch] = float(thr)
        written[ch] = float(thr)
    if not written:
        return {}
    cell_gating["thresh_frac_on"] = tfo
    existing["cell_gating"] = cell_gating
    try:
        write_json_atomic(info_path, existing)
    except OSError as exc:
        log.warning("Auto-threshold: cannot write %s: %s", info_path, exc)
        return {}
    return written


def _segmentation_tokens(args) -> set[str]:
    """Channel token(s) that ARE the segmentation input, lower-cased.

    ``--nuclear_token`` (already remapped onto ``--phase_token`` for the
    label-free methods by the time this runs) plus ``--cytoplasm_token`` under
    seeded watershed — the same pair that joins ``fluor_tokens_for_quant``.
    """
    toks = {str(getattr(args, "nuclear_token", "") or "").strip().lower()}
    if (getattr(args, "segmentation_method", "") == "stardist_seeded_watershed_cell"
            and getattr(args, "cytoplasm_token", "")):
        toks.add(str(args.cytoplasm_token).strip().lower())
    return {t for t in toks if t}


def run_auto_threshold_pass(args, output_dir: Path, fluor_tokens_for_quant: list[str],
                            log: logging.Logger) -> dict[str, float]:
    """Write the post-run per-channel ThreshFracOn defaults. Three modes:

    * ``--no_auto_threshold`` — skip entirely.
    * no control wells — Otsu on a pooled cell-mean / background-pixel
      distribution (:func:`pipeline.auto_threshold.estimate_thresholds_from_outputs`).
    * both control wells — the threshold maximising the relative fraction-on gap
      between them, read from the per-well CSVs this run just wrote.

    In both non-skip modes the segmentation channel is written **0**: it defines
    cell boundaries rather than being something to gate on. Zero is not absent —
    absent means the viewer's 50.0 default, whereas 0 counts every cell with a
    positive intensity as ON, which is how the channel is neutralised.

    Returns the channels actually written (existing values are never
    overwritten). Never raises: auto-threshold is informational and the run's
    real output is already on disk.
    """
    if getattr(args, "no_auto_threshold", False):
        log.info("Auto-threshold skipped (--no_auto_threshold).")
        return {}
    try:
        seg_tokens = _segmentation_tokens(args)
        non_seg = [c for c in fluor_tokens_for_quant
                   if str(c).strip().lower() not in seg_tokens]
        pos = str(getattr(args, "pos_control_well", "") or "").strip()
        neg = str(getattr(args, "neg_control_well", "") or "").strip()
        if pos and neg:
            log.info("Auto-threshold: control wells %s (positive) vs %s (negative).",
                     pos, neg)
            thresholds = estimate_thresholds_from_controls(
                output_dir,
                fluor_channels=non_seg,
                csv_prefix=args.csv_prefix,
                pos_well=pos,
                neg_well=neg,
                log=log,
            )
        else:
            thresholds = estimate_thresholds_from_outputs(
                output_dir,
                fluor_channels=non_seg,
                filename_schema=args.filename_schema,
                filename_sep=args.filename_sep,
                log=log,
            )
        # Which readout channels the estimate actually produced, before the
        # segmentation channel's unconditional zero joins them — the two say
        # very different things about the run and used to be reported as one.
        estimated = sorted(thresholds)
        for tok in seg_tokens:
            thresholds[tok] = 0.0
        written = _apply_thresholds_to_pipeline_info(output_dir, thresholds, log)
        # Estimating nothing for every readout channel is a failure, and it has
        # to be reported on its own terms rather than as part of the
        # what-got-written summary: the segmentation channel's unconditional
        # zero *is* a write, so it makes `written` truthy and the run reads as a
        # success that happened to produce one value. That is exactly how the
        # reported run looked.
        if non_seg and not estimated:
            log.warning(
                "Auto-threshold: no threshold could be estimated for any of %s. "
                "The per-well output archives yielded no usable image pairs — "
                "see the per-well lines above for why.",
                ", ".join(c.upper() for c in non_seg),
            )
        if written:
            log.info(
                "Auto-threshold defaults written for %d channel(s): %s",
                len(written),
                ", ".join(f"{ch.upper()}={v:.4g}" for ch, v in written.items()),
            )
        elif estimated or not non_seg:
            log.info("Auto-threshold pass produced no new defaults "
                     "(every channel already has a stored value).")
        return written
    except Exception as exc:
        log.warning("Auto-threshold pass failed: %s", exc)
        return {}
