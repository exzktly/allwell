"""StarDist nuclear segmentation for the pipeline.

Extracted from ``process_microscopy.py`` so the StarDist segmenters and their
per-worker model state live behind one boundary. **TensorFlow / StarDist /
csbdeep are imported lazily** (``normalize`` inside ``_segment_stardist_nuclei``;
the model is loaded by ``process_microscopy._worker_init`` and injected via
:func:`set_model`), so importing this module does *not* load TensorFlow — the
model is just ``None`` until a worker initialises it.

Worker-global state lives here and is mutated through :func:`set_model` /
:func:`set_fov_label`:
- ``_STARDIST_MODEL`` — the loaded StarDist2D model (per worker process).
- ``_SEG_FOV_LABEL`` — the FOV name currently being segmented (diagnostics).

``process_microscopy`` re-exports the two ``_segment_stardist_*`` functions, so
the segmentation dispatch is unchanged.
"""

from __future__ import annotations

import logging
import os
import time

import numpy as np
from scipy import ndimage as ndi
from skimage.filters import threshold_otsu
from skimage.segmentation import watershed

from pipeline.background import _isotropic_disk_tophat

# Log under the pipeline's logger name so diagnostic lines (e.g. the per-FOV
# ``seg-input`` line, the last thing printed before a ClipperLib abort) are
# unchanged from when this lived in process_microscopy.
log = logging.getLogger("process_microscopy")

# Per-worker StarDist model + the FOV label for diagnostics. Set by
# process_microscopy._worker_init / process_image_group via the setters below.
_STARDIST_MODEL = None
_SEG_FOV_LABEL: str = ""

# StarDist sees percentile-normalized input anchored to [0, 1]; clamp to this so
# no out-of-distribution value (e.g. a dim region amplified by empty-well
# flat-field division) can drive the regressed polygon vertices out of
# ClipperLib's integer range — the uncatchable "coordinate outside allowed
# range" abort.
_STARDIST_INPUT_CLIP = (0.0, 1.0)


def set_model(model) -> None:
    """Install the per-worker StarDist model (called from ``_worker_init``)."""
    global _STARDIST_MODEL
    _STARDIST_MODEL = model


def set_fov_label(name: str) -> None:
    """Record the FOV being segmented, for the diagnostic ``seg-input`` line."""
    global _SEG_FOV_LABEL
    _SEG_FOV_LABEL = name


def _segment_stardist_nuclei(nir_corr: np.ndarray) -> np.ndarray:
    from csbdeep.utils import normalize
    if _STARDIST_MODEL is None:
        raise RuntimeError(
            "StarDist model is not initialised. "
            "Ensure _worker_init ran before process_image_group."
        )
    # StarDist's polygon non-maximum-suppression uses ClipperLib, which casts
    # polygon coordinates to integers and aborts the whole *process* with an
    # uncatchable C++ exception —
    #   terminate called after throwing an instance of
    #   'ClipperLib::clipperException'  ("Coordinate outside allowed range")
    # — if the network is fed non-finite OR out-of-distribution input. Two ways
    # in: (1) a single NaN/Inf pixel — ``normalize`` uses ``np.percentile`` (not
    # the nan-aware variant), so one NaN turns the percentiles NaN and the whole
    # image non-finite; (2) huge *finite* values — empty-well flat-field divides
    # by a near-zero field and amplifies a dim region by ~100x, and the default
    # ``normalize(clip=False)`` does NOT clip the top end, so those outliers reach
    # the network and the regressed distances blow up. So: scrub non-finite, then
    # normalize WITH ``clip=True`` and an explicit clamp so the input is strictly
    # in [0, 1] — the distribution StarDist was trained on — making the abort
    # impossible regardless of the upstream correction.
    arr = np.asarray(nir_corr, dtype=np.float32)
    if not np.all(np.isfinite(arr)):
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    nir_norm = normalize(arr, 1, 99.8, clip=True)
    if not np.all(np.isfinite(nir_norm)):
        nir_norm = np.nan_to_num(nir_norm, nan=0.0, posinf=0.0, neginf=0.0)
    np.clip(nir_norm, _STARDIST_INPUT_CLIP[0], _STARDIST_INPUT_CLIP[1], out=nir_norm)

    # ── ClipperLib diagnostics ───────────────────────────────────────────────
    # The abort ("coordinate outside allowed range") happens *inside*
    # predict_instances, in StarDist's polygon NMS, when the regressed vertices
    # land at out-of-range integer coordinates — the classic cause being a
    # pathological / near-constant input (e.g. an empty-well over-subtracted
    # nuclear field). It's an uncatchable C++ abort, so we can't try/except it;
    # instead log the input characteristics for *every* FOV. The last
    # ``seg-input`` line before the process dies names the culprit and its stats.
    fov = _SEG_FOV_LABEL or "?"
    lo, hi = float(nir_norm.min()), float(nir_norm.max())
    log.info("  seg-input %s: shape=%s min=%.4g max=%.4g mean=%.4g dyn_range=%.4g "
             "finite=%.0f%% nonzero=%.0f%%",
             fov, tuple(nir_norm.shape), lo, hi, float(nir_norm.mean()), hi - lo,
             float(np.isfinite(nir_norm).mean()) * 100.0,
             float(np.count_nonzero(nir_norm)) / nir_norm.size * 100.0)

    # A near-constant field has no nuclei and is the classic ClipperLib trigger —
    # skip prediction (0 nuclei) rather than risk the uncatchable abort.
    if hi - lo < 1e-6:
        log.warning("  seg-input %s: near-constant field (dyn_range≈0) — skipping "
                    "StarDist (0 nuclei) to avoid a ClipperLib abort", fov)
        return np.zeros(nir_norm.shape, dtype=np.int32)

    # Opt-in deep trace (ALLWELL_SEG_DEBUG=1): run the raw network once to log the
    # predicted probability / radial-distance magnitudes. Huge or non-finite
    # distances are the *direct* source of the out-of-range polygon coordinates;
    # this pins the failure to prediction vs input. Fully guarded — never alters
    # the production path, and falls back silently if the split API differs.
    if os.environ.get("ALLWELL_SEG_DEBUG"):
        try:
            pred = _STARDIST_MODEL.predict(nir_norm)
            prob, dist = pred[0], pred[1]
            log.info("  seg-debug %s: prob[min=%.3g max=%.3g] dist[min=%.3g "
                     "max=%.3g] dist_nonfinite=%d prob_shape=%s dist_shape=%s",
                     fov, float(np.nanmin(prob)), float(np.nanmax(prob)),
                     float(np.nanmin(dist)), float(np.nanmax(dist)),
                     int((~np.isfinite(dist)).sum()),
                     tuple(np.shape(prob)), tuple(np.shape(dist)))
        except Exception as exc:                                # noqa: BLE001
            log.warning("  seg-debug %s: predict() trace failed: %s", fov, exc)

    _t0 = time.perf_counter()
    labels, _ = _STARDIST_MODEL.predict_instances(nir_norm)
    # Per-FOV inference timing: a slow per-call number points at CPU contention /
    # thread starvation (vs the run merely doing more FOVs). Pairs with the
    # ``seg-input`` line above so each FOV's input stats + predict cost sit
    # together in the log.
    log.info("  seg %s: predict_instances %.1fs (%d nuclei)",
             fov, time.perf_counter() - _t0, int(labels.max()) if labels.size else 0)
    return labels.astype(np.int32)


def _segment_stardist_seeded_watershed_cell(
    nir_corr: np.ndarray,
    cytoplasm_raw: np.ndarray,
    tophat_radius_fluor: int,
    min_nucleus_area_px: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    stardist_labels = _segment_stardist_nuclei(nir_corr)
    nucleus_ids = np.unique(stardist_labels)
    nucleus_ids = nucleus_ids[nucleus_ids != 0]
    filtered_ids: list[int] = []
    for nid in nucleus_ids:
        area = int(np.count_nonzero(stardist_labels == nid))
        if area >= min_nucleus_area_px:
            filtered_ids.append(int(nid))

    nuclear_points = np.zeros_like(stardist_labels, dtype=np.int32)
    marker_id = 1
    for nid in filtered_ids:
        cy, cx = ndi.center_of_mass(stardist_labels == nid)
        if np.isnan(cy) or np.isnan(cx):
            continue
        y = int(np.clip(round(float(cy)), 0, stardist_labels.shape[0] - 1))
        x = int(np.clip(round(float(cx)), 0, stardist_labels.shape[1] - 1))
        nuclear_points[y, x] = marker_id
        marker_id += 1

    # Isotropic disk top-hat (not the square apply_tophat): a square footprint
    # here stamps square bias onto the cytoplasm mask → square-edged watershed
    # cells, regardless of the chosen --background method.
    cytoplasm_tophat = _isotropic_disk_tophat(cytoplasm_raw, tophat_radius_fluor)
    try:
        thresh = threshold_otsu(cytoplasm_tophat)
    except ValueError:
        thresh = float(np.mean(cytoplasm_tophat))
    cytoplasm_binary_mask = cytoplasm_tophat > thresh
    distance = ndi.distance_transform_edt(cytoplasm_binary_mask)
    if marker_id <= 1:
        labels = np.zeros_like(stardist_labels, dtype=np.int32)
    else:
        labels = watershed(
            -distance,
            markers=nuclear_points,
            mask=cytoplasm_binary_mask,
        ).astype(np.int32)
    return labels, nuclear_points, cytoplasm_tophat, cytoplasm_binary_mask
