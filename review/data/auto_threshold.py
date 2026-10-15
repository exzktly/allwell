"""Auto-threshold default cell-gating values via Otsu on a balanced
per-cell / background-pixel distribution.

The Cell Gating tab lets the user set a per-channel ThreshFracOn
default — the fraction-on cut used to highlight a channel's "on"
population in downstream plots. Picking a good default by hand is
tedious, so this module derives one automatically:

1. For every loaded well, list the available timepoints (sorted
   chronologically) and pick the **first**, **middle**, and **last**.
2. For each of those (FOV, timepoint) pairs and each fluorescence
   channel, load the segmentation mask and the (tophat-corrected)
   fluorescence image.
3. For every cell in the image, compute its mean intensity and pick
   *one* random pixel that lies outside any cell. The pair contributes
   one "on-cell" value and one "off-cell" value to the channel's
   pooled distribution.
4. Run Otsu's method on each channel's distribution. The threshold
   value naturally separates background-pixel intensities from
   cell-mean intensities and is returned as the channel default.

Two entry points share the algorithm:

* :func:`compute_auto_thresholds` — pure-Python helper that walks an
  on-disk results directory. Called from the end of the
  ``process_microscopy`` pipeline and from the runtime Cell Gating
  tab's "Auto-threshold" button.
* :func:`apply_auto_thresholds_to_pipeline_info` — convenience that
  computes the thresholds and merges them into ``pipeline_info.json``
  via the existing :mod:`review.gating_state` writer.

Logging
-------
The module uses ``logging.getLogger("review.data.auto_threshold")``.
The runtime app's log drawer (``allwell._attach_log_ring_buffer``)
already captures every logger record, so streaming progress messages
to the drawer is free — callers don't have to pass a logger in.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from collections.abc import Callable, Iterable

import numpy as np

from pipeline.auto_threshold import (
    DEFAULT_CELLS_PER_IMAGE_CAP,
    pick_endpoint_timepoints as _pick_endpoint_timepoints,
    sample_cell_and_bg as _sample_cell_and_bg,
)
from pipeline.threshold_optimize import round_stored_threshold
from core.safe import swallowed
from review.data.frame_load import referenced_phase_channel


logger = logging.getLogger("review.data.auto_threshold")

#: ``skimage.filters.threshold_otsu``, bound on first use rather than at import:
#: skimage is a heavy dependency and this module is imported by the Review shell
#: whether or not anyone presses Auto-threshold. A test may set it directly to
#: drive :func:`thresholds_from_samples` with a known value.
_threshold_otsu = None


# ── Image loading ──────────────────────────────────────────────────────────


def _open_image_at(ref) -> np.ndarray | None:
    """Open an ``ImgRef`` (disk or zip member) as a 2-D numpy array."""
    if ref is None:
        return None
    try:
        from review.data.image_discovery import open_imgref_as_array
        arr = open_imgref_as_array(ref, greyscale=False)
    except Exception as exc:
        logger.debug("auto_threshold: failed to open image %r: %s", ref, exc)
        return None
    if arr is None:
        return None
    arr = np.asarray(arr)
    if arr.ndim == 3:
        # Multi-channel — collapse to the first channel as a fallback;
        # the pipeline writes single-channel tophat TIFs so this branch
        # is mostly defensive.
        arr = arr[..., 0]
    return arr


# Core sampler (`_sample_cell_and_bg`) is imported from
# `auto_threshold_core` at the top of this module.


# ── Walk the output dir ────────────────────────────────────────────────────


def _iter_well_zips(out_dir: Path) -> list[Path]:
    """Return the ``<WELL>_out.zip`` files in *out_dir*, sorted by name."""
    out: list[Path] = []
    for p in sorted(out_dir.glob("*_out.zip")):
        if p.name.startswith("."):
            continue
        out.append(p)
    return out


def _with_raw_frames(tophat: dict | None, raw: dict | None) -> dict:
    """*tophat*, completed from *raw* for every ``(fov, tp)`` it lacks.

    Only ever applied to the referenced phase channel, where the two are the
    same pixels. An archived ``_tophat`` still wins, so a well an earlier
    copy-mode run wrote is read from its own archive."""
    return {**(raw or {}), **(tophat or {})}


def _scan_well_images(
    zip_path: Path,
    fluor_token: str,
    pipeline_info: dict | None,
):
    """Wrapper around ``review.image_discovery.scan_zip_members`` that
    returns the mask + tophat_fluor dicts for one well + one channel."""
    from review.data.image_discovery import scan_zip_members
    _g, _ov, mask, tophat, _sm = scan_zip_members(
        zip_path,
        fluor_token.lower(),
        _pipeline_info=pipeline_info,
    )
    return mask, tophat


def _input_zip_for(zip_path: Path, in_dir: Path | None) -> Path | None:
    """The ``<WELL>.zip`` input archive beside *zip_path*'s ``<WELL>_out.zip``."""
    if in_dir is None or not zip_path.name.endswith("_out.zip"):
        return None
    candidate = Path(in_dir) / f"{zip_path.name[: -len('_out.zip')]}.zip"
    return candidate if candidate.is_file() else None


def _gather_via_zip(zip_path: Path, channels: list[str], pipeline_info,
                    in_dir: Path | None = None):
    """Return ``{channel: (mask_refs, fluor_refs)}`` for one well zip.

    The referenced phase channel (:func:`referenced_phase_channel`) is
    completed from the well's input archive in *in_dir*, when there is one.
    """
    from review.data.image_discovery import scan_zip_members
    phase = referenced_phase_channel(pipeline_info)
    out: dict[str, tuple[dict, dict]] = {}
    for ch in channels:
        try:
            m, f = _scan_well_images(zip_path, ch, pipeline_info)
            in_zip = _input_zip_for(zip_path, in_dir) if ch.lower() == phase else None
            if in_zip is not None:
                raw = scan_zip_members(in_zip, ch.lower(), _pipeline_info=pipeline_info)[0]
                f = _with_raw_frames(f, raw)
        except Exception as exc:
            logger.warning("auto_threshold: %s ch=%s scan failed: %s",
                           zip_path.name, ch, exc)
            continue
        out[ch] = (m or {}, f or {})
    return out


def _gather_via_find(target, channels: list[str], pipeline_info):
    """Multi-layout discovery via :func:`find_well_images_and_masks`.

    ``target`` is ``(well_label, data_dir, in_dir)``.  Returns
    ``{channel: (mask_refs, fluor_refs)}`` where the fluor refs are the
    tophat-corrected images (matching the pipeline's behaviour).
    """
    well_label, data_dir, in_dir_path = target
    from review.data.image_discovery import find_well_images_and_masks
    phase = referenced_phase_channel(pipeline_info)
    out: dict[str, tuple[dict, dict]] = {}
    for ch in channels:
        try:
            _g, _ov, mask, tophat = find_well_images_and_masks(
                data_dir, well_label, fluor_token=ch, in_dir=in_dir_path,
                _pipeline_info=pipeline_info,
            )
        except Exception as exc:
            logger.warning("auto_threshold: well=%s ch=%s discovery failed: %s",
                           well_label, ch, exc)
            continue
        if ch.lower() == phase:
            tophat = _with_raw_frames(tophat, _g)
        out[ch] = (mask or {}, tophat or {})
    return out


def _read_pipeline_info(out_dir: Path) -> dict | None:
    """Load ``pipeline_info.json`` from *out_dir* (or its parent) if present."""
    import json
    for candidate in (out_dir / "pipeline_info.json",
                      out_dir.parent / "pipeline_info.json"):
        try:
            if candidate.exists():
                data = json.loads(candidate.read_text())
                if isinstance(data, dict):
                    return data
        except Exception as exc:
            logger.warning("auto_threshold: could not read %s: %s",
                           candidate, exc)
    return None


# ── Public API ─────────────────────────────────────────────────────────────


def compute_auto_thresholds(
    out_dir: Path,
    *,
    fluor_channels: Iterable[str],
    cells_per_image_cap: int = DEFAULT_CELLS_PER_IMAGE_CAP,
    progress: Callable[[str], None] | None = None,
    rng_seed: int | None = None,
    well_labels: Iterable[str] | None = None,
    in_dir: Path | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, float]:
    """Compute the auto-threshold per channel by Otsu on a per-cell + bg
    distribution sampled from the first / middle / last timepoint of every
    FOV in every loaded well.

    Returns a dict ``{channel: threshold}``. Channels that produced no
    samples (e.g. no masks loaded) are omitted from the result.

    ``progress`` is invoked with human-readable status strings so the
    caller (the Cell Gating tab) can stream them to the log drawer. The
    module-level logger receives the same strings.
    """
    def _cancelled() -> bool:
        """Whether the caller has asked this pass to stop.

        An explicit predicate rather than an exception raised from the progress
        callback: ``_emit`` wraps that callback in ``except Exception``, so a
        cancel signalled that way is swallowed and the scan runs to completion
        anyway — reading every remaining well for a result nobody will use.
        """
        if should_cancel is None:
            return False
        try:
            return bool(should_cancel())
        except Exception:
            # Treated as "not cancelled" so a broken predicate cannot abort a
            # scan that was never asked to stop — but said out loud, because
            # the other reading is a pass that has become uncancellable, and
            # this function exists precisely because a swallowed cancel is
            # indistinguishable from one nobody sent.
            swallowed("auto-threshold: should_cancel raised; treating as not cancelled")
            return False

    channels = [str(ch).strip().lower() for ch in fluor_channels if str(ch).strip()]
    if not channels:
        return {}

    out_dir = Path(out_dir) if out_dir else None

    def _emit(msg: str) -> None:
        logger.info(msg)
        if progress is not None:
            try:
                progress(msg)
            except Exception:
                swallowed()  # was: pass (silent except)

    if out_dir is None or not out_dir.exists():
        _emit(f"Auto-threshold: data directory not found ({out_dir!r}).")
        return {}

    # Checked here, before the walk, so a missing skimage costs nothing rather
    # than being discovered after every well has been read. Bound on the module
    # so ``thresholds_from_samples`` can be driven with a known value.
    global _threshold_otsu
    if _threshold_otsu is None:
        try:
            from skimage.filters import threshold_otsu
        except Exception as exc:  # third party guard
            _emit(f"Auto-threshold: skimage.filters.threshold_otsu "
                  f"unavailable: {exc}")
            return {}
        _threshold_otsu = threshold_otsu

    pipeline_info = _read_pipeline_info(out_dir)
    rng = random.Random(rng_seed)
    per_channel: dict[str, list[float]] = {ch: [] for ch in channels}
    # Count decode attempts vs failures per channel (audit E9). ``_open_image_at``
    # swallows a decode error to DEBUG and returns None, so a channel whose
    # tophat images are largely unreadable would silently estimate its Otsu
    # threshold from a biased subset. We WARN in the summary when a meaningful
    # fraction was dropped.
    per_channel_attempted: dict[str, int] = {ch: 0 for ch in channels}
    per_channel_decode_failed: dict[str, int] = {ch: 0 for ch in channels}

    well_list = [str(w).strip() for w in (well_labels or []) if str(w).strip()]
    in_dir_path = Path(in_dir) if in_dir else None

    if well_list:
        _emit(f"Auto-threshold: scanning {len(well_list)} well(s) "
              f"({', '.join(well_list[:6])}{'…' if len(well_list) > 6 else ''}) "
              f"for channels: {', '.join(channels)}")
        _scan_one_well = _gather_via_find  # multi-layout discovery
        targets: list = [(label, out_dir, in_dir_path) for label in well_list]
        label_of = lambda t: t[0]
    else:
        well_zips = _iter_well_zips(out_dir)
        if not well_zips:
            _emit(f"Auto-threshold: no processed wells in {out_dir}")
            return {}
        _emit(f"Auto-threshold: scanning {len(well_zips)} well(s) for "
              f"channels: {', '.join(channels)}")
        def _scan_one_well(z, chans, info):
            return _gather_via_zip(z, chans, info, in_dir=in_dir_path)
        targets = list(well_zips)
        label_of = lambda t: Path(t).name

    for w_idx, target in enumerate(targets, start=1):
        if _cancelled():
            _emit("Auto-threshold: cancelled.")
            return {}
        try:
            per_channel_refs = _scan_one_well(target, channels, pipeline_info)
        except Exception as exc:
            logger.warning("auto_threshold: skipping %s — scan failed: %s",
                           label_of(target), exc)
            continue
        if not per_channel_refs:
            _emit(f"Auto-threshold: no images found for {label_of(target)}")
            continue

        ref_mask = next((m for (m, _f) in per_channel_refs.values() if m), {})
        if not ref_mask:
            _emit(f"Auto-threshold: no masks for {label_of(target)} — skipped")
            continue
        all_tps = {tp for (_fov, tp) in ref_mask.keys()}
        pick_tps = _pick_endpoint_timepoints(all_tps)
        if not pick_tps:
            continue
        _emit(f"Auto-threshold: {label_of(target)} → timepoints "
              f"{', '.join(pick_tps)}")

        for ch in channels:
            mask_refs, fluor_refs = per_channel_refs.get(ch, ({}, {}))
            if not mask_refs or not fluor_refs:
                continue
            for (fov, tp), mask_ref in mask_refs.items():
                if tp not in pick_tps:
                    continue
                fluor_ref = fluor_refs.get((fov, tp))
                if fluor_ref is None:
                    continue
                per_channel_attempted[ch] += 1
                labels = _open_image_at(mask_ref)
                fluor = _open_image_at(fluor_ref)
                if labels is None or fluor is None:
                    per_channel_decode_failed[ch] += 1
                    continue
                cell_means, bg_values = _sample_cell_and_bg(
                    labels, fluor, cap=cells_per_image_cap, rng=rng,
                )
                if not cell_means:
                    continue
                per_channel[ch].extend(cell_means)
                per_channel[ch].extend(bg_values)
        _emit(f"Auto-threshold: completed well "
              f"{w_idx}/{len(targets)} ({label_of(target)})")

    return thresholds_from_samples(
        channels, per_channel, per_channel_attempted,
        per_channel_decode_failed, _emit)


def thresholds_from_samples(
    channels: Iterable[str],
    per_channel: dict[str, list[float]],
    attempted_by_channel: dict[str, int],
    failed_by_channel: dict[str, int],
    emit: Callable[[str], None],
) -> dict[str, float]:
    """Per-channel Otsu thresholds from already-gathered samples.

    Separated from the walk above so the *decision* can be driven without an
    output folder: the gathering is IO and the rounding is arithmetic, and the
    arithmetic is where this went wrong. It is also the seam the sweep guard
    reads — every estimator in the app must round through one rule.
    """
    thresholds: dict[str, float] = {}
    for ch in channels:
        attempted = attempted_by_channel.get(ch, 0)
        failed = failed_by_channel.get(ch, 0)
        if attempted and failed / attempted >= 0.25:
            logger.warning(
                "auto_threshold: %s — %d/%d image pair(s) failed to decode "
                "(%.0f%%); the threshold is estimated from the remaining "
                "subset and may be biased.",
                ch.upper(), failed, attempted, 100.0 * failed / attempted,
            )
        values = per_channel.get(ch) or []
        if len(values) < 2:
            emit(f"Auto-threshold: skipping {ch.upper()} — only "
                 f"{len(values)} sample(s).")
            continue
        arr = np.asarray(values, dtype=np.float64)
        # Otsu fails on a constant array; guard.
        if arr.max() <= arr.min():
            emit(f"Auto-threshold: skipping {ch.upper()} — constant "
                 f"distribution (min == max == {arr.min():.3g}).")
            continue
        otsu = _threshold_otsu
        if otsu is None:                    # a direct caller, not through the walk
            from skimage.filters import threshold_otsu as otsu
        try:
            # The pipeline's rule, imported rather than restated: round for
            # readability without rounding the value away. ``round()`` alone is
            # right for camera counts — a cut of 547.7 reads better as 548 and
            # is what the user can type back in — and catastrophic below 1. A
            # channel normalised to 0–1 is a real input (``_safe_imwrite``
            # writes float32), and its Otsu threshold of 0.2496 became ``0.0``:
            # a gate that passes every cell, stored as the one value that also
            # means "this channel was never measured". Measured on a 0–1
            # channel: 0.25 gates 50.0% of cells, 0.0 gates 100.0%.
            #
            # This is the third estimator to answer this question, and it is
            # the one behind the Cell Gating tab's Auto-threshold button —
            # fixing the two pipeline-side ones and not this is the half-sweep
            # CLAUDE.md §7.5 #5 warns about, with the interactive path left
            # holding the defect.
            thr = round_stored_threshold(float(otsu(arr)))
        except Exception as exc:
            emit(f"Auto-threshold: Otsu failed for {ch.upper()}: {exc}")
            continue
        thresholds[ch] = thr
        emit(f"Auto-threshold: {ch.upper()} → {thr:.4g} (n={arr.size})")

    return thresholds


def apply_auto_thresholds_to_pipeline_info(
    out_dir: Path,
    *,
    fluor_channels: Iterable[str],
    progress: Callable[[str], None] | None = None,
    overwrite_existing: bool = False,
) -> dict[str, float]:
    """Compute the auto-thresholds for *out_dir* and merge them into the
    ``pipeline_info.json`` cell-gating block.

    When ``overwrite_existing`` is False (default), channels that already
    have a non-default ThreshFracOn value persisted are left alone so a
    user-curated threshold isn't silently overwritten.

    Returns the dict of values that were actually written.
    """
    from review.domain.gating_state import (
        build_gating_block,
        read_gating_params,
        read_nuclear_token,
        save_gating_to_pipeline_info,
    )

    out_dir = Path(out_dir)
    computed = compute_auto_thresholds(
        out_dir,
        fluor_channels=fluor_channels,
        progress=progress,
    )
    if not computed:
        return {}

    existing = read_gating_params(out_dir) or {}
    existing_fluor = dict(existing.get("fluor_gates") or {})
    existing_tfo = dict(existing.get("thresh_frac_on") or {})
    # This pass rebuilds the block from named keys, so anything it does not read
    # is dropped on write. The count cutoffs are nothing to do with an intensity
    # sweep — carry them through untouched.
    existing_tco = dict(existing.get("thresh_count_on") or {})
    # Preserve any persisted scatter-gate inclusion state — this rebuild only
    # touches ThreshFracOn, so it must not drop the gates the user drew.
    existing_scatter = existing.get("scatter_gates")
    existing_use_scatter = bool(existing.get("use_scatter_gates", False))
    existing_use_thresh = bool(existing.get("use_scatter_thresh", False))
    # Per-layer gate selections, migrating older single-selection schemas.
    _legacy_sel = existing.get("scatter_gate_choices")
    if _legacy_sel is None and existing.get("scatter_gate_choice"):
        _legacy_sel = [existing["scatter_gate_choice"]]
    existing_choices_gating = existing.get("scatter_gate_choices_gating", _legacy_sel)
    existing_choices_thresh = existing.get("scatter_gate_choices_thresh", _legacy_sel)

    written: dict[str, float] = {}
    for ch, thr in computed.items():
        if not overwrite_existing and ch in existing_tfo:
            continue
        existing_tfo[ch] = float(thr)
        written[ch] = float(thr)

    if not written:
        return {}

    block = build_gating_block(
        existing_fluor, existing_tfo,
        scatter_gates=existing_scatter,
        use_scatter_gates=existing_use_scatter,
        scatter_gate_choices_gating=existing_choices_gating,
        scatter_gate_choices_thresh=existing_choices_thresh,
        use_scatter_thresh=existing_use_thresh,
        # The nuclear channel's ThreshFracOn default is not the one every other
        # channel gets, so ``build_gating_block`` has to know which channel is
        # nuclear before it diffs against defaults. Called without it, a nuclear
        # cutoff sitting at the ordinary readout default read as "unchanged" and
        # was dropped on write, while the zero it starts at was written out as
        # though the user had chosen it. ARCHITECTURE §5.7 names this call as
        # one of the three sites that must pass the token.
        seg_channel_token=read_nuclear_token(out_dir),
        thresh_count_on=existing_tco,
    )
    save_gating_to_pipeline_info(out_dir, block)
    return written
