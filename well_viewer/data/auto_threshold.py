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
  via the existing :mod:`well_viewer.gating_state` writer.

Logging
-------
The module uses ``logging.getLogger("well_viewer.data.auto_threshold")``.
The runtime app's log drawer (``all_well._attach_log_ring_buffer``)
already captures every logger record, so streaming progress messages
to the drawer is free — callers don't have to pass a logger in.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import numpy as np

from auto_threshold_core import (
    DEFAULT_CELLS_PER_IMAGE_CAP,
    parse_tp_hours as _parse_tp_hours,
    pick_endpoint_timepoints as _pick_endpoint_timepoints,
    sample_cell_and_bg as _sample_cell_and_bg,
)


logger = logging.getLogger("well_viewer.data.auto_threshold")


# ── Image loading ──────────────────────────────────────────────────────────


def _open_image_at(ref) -> Optional[np.ndarray]:
    """Open an ``ImgRef`` (disk or zip member) as a 2-D numpy array."""
    if ref is None:
        return None
    try:
        from well_viewer.data.image_discovery import open_imgref_as_array
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


def _iter_well_zips(out_dir: Path) -> List[Path]:
    """Return the ``<WELL>_out.zip`` files in *out_dir*, sorted by name."""
    out: List[Path] = []
    for p in sorted(out_dir.glob("*_out.zip")):
        if p.name.startswith("."):
            continue
        out.append(p)
    return out


def _scan_well_images(
    zip_path: Path,
    fluor_token: str,
    pipeline_info: Optional[dict],
):
    """Wrapper around ``well_viewer.image_discovery.scan_zip_members`` that
    returns the mask + tophat_fluor dicts for one well + one channel."""
    from well_viewer.data.image_discovery import scan_zip_members
    _g, _ov, mask, tophat, _sm = scan_zip_members(
        zip_path,
        fluor_token.lower(),
        _pipeline_info=pipeline_info,
    )
    return mask, tophat


def _gather_via_zip(zip_path: Path, channels: List[str], pipeline_info):
    """Return ``{channel: (mask_refs, fluor_refs)}`` for one well zip."""
    out: Dict[str, Tuple[dict, dict]] = {}
    for ch in channels:
        try:
            m, f = _scan_well_images(zip_path, ch, pipeline_info)
        except Exception as exc:
            logger.warning("auto_threshold: %s ch=%s scan failed: %s",
                           zip_path.name, ch, exc)
            continue
        out[ch] = (m or {}, f or {})
    return out


def _gather_via_find(target, channels: List[str], pipeline_info):
    """Multi-layout discovery via :func:`find_well_images_and_masks`.

    ``target`` is ``(well_label, data_dir, in_dir)``.  Returns
    ``{channel: (mask_refs, fluor_refs)}`` where the fluor refs are the
    tophat-corrected images (matching the pipeline's behaviour).
    """
    well_label, data_dir, in_dir_path = target
    from well_viewer.data.image_discovery import find_well_images_and_masks
    out: Dict[str, Tuple[dict, dict]] = {}
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
        out[ch] = (mask or {}, tophat or {})
    return out


def _read_pipeline_info(out_dir: Path) -> Optional[dict]:
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
    progress: Optional[Callable[[str], None]] = None,
    rng_seed: Optional[int] = None,
    well_labels: Optional[Iterable[str]] = None,
    in_dir: Optional[Path] = None,
) -> Dict[str, float]:
    """Compute the auto-threshold per channel by Otsu on a per-cell + bg
    distribution sampled from the first / middle / last timepoint of every
    FOV in every loaded well.

    Returns a dict ``{channel: threshold}``. Channels that produced no
    samples (e.g. no masks loaded) are omitted from the result.

    ``progress`` is invoked with human-readable status strings so the
    caller (the Cell Gating tab) can stream them to the log drawer. The
    module-level logger receives the same strings.
    """
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
                pass

    if out_dir is None or not out_dir.exists():
        _emit(f"Auto-threshold: data directory not found ({out_dir!r}).")
        return {}

    try:
        from skimage.filters import threshold_otsu as _threshold_otsu
    except Exception as exc:  # noqa: BLE001 - third party guard
        _emit(f"Auto-threshold: skimage.filters.threshold_otsu unavailable: {exc}")
        return {}

    pipeline_info = _read_pipeline_info(out_dir)
    rng = random.Random(rng_seed)
    per_channel: Dict[str, List[float]] = {ch: [] for ch in channels}

    well_list = [str(w).strip() for w in (well_labels or []) if str(w).strip()]
    in_dir_path = Path(in_dir) if in_dir else None

    if well_list:
        _emit(f"Auto-threshold: scanning {len(well_list)} well(s) "
              f"({', '.join(well_list[:6])}{'…' if len(well_list) > 6 else ''}) "
              f"for channels: {', '.join(channels)}")
        _scan_one_well = _gather_via_find  # multi-layout discovery
        targets: List = [(label, out_dir, in_dir_path) for label in well_list]
        label_of = lambda t: t[0]
    else:
        well_zips = _iter_well_zips(out_dir)
        if not well_zips:
            _emit(f"Auto-threshold: no processed wells in {out_dir}")
            return {}
        _emit(f"Auto-threshold: scanning {len(well_zips)} well(s) for "
              f"channels: {', '.join(channels)}")
        _scan_one_well = _gather_via_zip
        targets = list(well_zips)
        label_of = lambda t: Path(t).name

    for w_idx, target in enumerate(targets, start=1):
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
                labels = _open_image_at(mask_ref)
                fluor = _open_image_at(fluor_ref)
                if labels is None or fluor is None:
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

    thresholds: Dict[str, float] = {}
    for ch in channels:
        values = per_channel.get(ch) or []
        if len(values) < 2:
            _emit(f"Auto-threshold: skipping {ch.upper()} — only "
                  f"{len(values)} sample(s).")
            continue
        arr = np.asarray(values, dtype=np.float64)
        # Otsu fails on a constant array; guard.
        if arr.max() <= arr.min():
            _emit(f"Auto-threshold: skipping {ch.upper()} — constant "
                  f"distribution (min == max == {arr.min():.3g}).")
            continue
        try:
            # Round to the nearest integer before saving — the gating cut is an
            # intensity value; a clean integer default matches the pipeline's
            # auto-threshold and what the user can type back in.
            thr = float(round(float(_threshold_otsu(arr))))
        except Exception as exc:
            _emit(f"Auto-threshold: Otsu failed for {ch.upper()}: {exc}")
            continue
        thresholds[ch] = thr
        _emit(f"Auto-threshold: {ch.upper()} → {thr:.4g} "
              f"(n={arr.size})")

    return thresholds


def apply_auto_thresholds_to_pipeline_info(
    out_dir: Path,
    *,
    fluor_channels: Iterable[str],
    progress: Optional[Callable[[str], None]] = None,
    overwrite_existing: bool = False,
) -> Dict[str, float]:
    """Compute the auto-thresholds for *out_dir* and merge them into the
    ``pipeline_info.json`` cell-gating block.

    When ``overwrite_existing`` is False (default), channels that already
    have a non-default ThreshFracOn value persisted are left alone so a
    user-curated threshold isn't silently overwritten.

    Returns the dict of values that were actually written.
    """
    from well_viewer.domain.gating_state import (
        build_gating_block,
        read_gating_params,
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

    written: Dict[str, float] = {}
    for ch, thr in computed.items():
        if not overwrite_existing and ch in existing_tfo:
            continue
        existing_tfo[ch] = float(thr)
        written[ch] = float(thr)

    if not written:
        return {}

    block = build_gating_block(existing_fluor, existing_tfo)
    save_gating_to_pipeline_info(out_dir, block)
    return written
