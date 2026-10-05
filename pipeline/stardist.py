"""StarDist nuclear segmentation for the pipeline.

Extracted from ``process_microscopy.py`` so the StarDist segmenters and their
per-worker model state live behind one boundary. **TensorFlow / StarDist /
csbdeep are imported lazily** (``normalize`` inside ``_segment_stardist_nuclei``;
the model is loaded by ``process_microscopy._worker_init`` and injected via
:func:`set_model`), so importing this module does *not* load TensorFlow — the
model is just ``None`` until a worker initialises it.

Worker-global state lives here and is mutated through :func:`set_model` /
:func:`set_params` / :func:`set_fov_label`:
- ``_STARDIST_MODEL`` — the loaded StarDist2D model (per worker process).
- ``_STARDIST_PARAMS`` — the run's :class:`StarDistParams` (detection thresholds
  + the tiling cap), installed from the ``--stardist_*`` CLI args.
- ``_SEG_FOV_LABEL`` — the FOV name currently being segmented (diagnostics).

The inference itself is :func:`segment_nuclei`, which takes the model and the
parameters as arguments and reads no module state, so an interactive caller can
run the pipeline's exact segmentation on one frame.
``_segment_stardist_nuclei`` is the worker-global wrapper over it.

``process_microscopy`` imports the two ``_segment_stardist_*`` functions for its
segmentation dispatch.
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, replace

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


@dataclass(frozen=True)
class StarDistParams:
    """The per-run StarDist knobs, as one immutable value.

    ``prob_thresh`` / ``nms_thresh`` are ``None`` by default, which means "use
    the threshold the pretrained model ships in its own ``thresholds.json``"
    (≈ 0.479 / 0.3 for ``2D_versatile_fluo``) — that is also what
    ``predict_instances`` does with ``None``, so the default path is byte-for-byte
    the behaviour of passing nothing at all.

    * ``prob_thresh`` — object-probability cut applied to the network's
      objectness map before polygon NMS. LOWER detects more nuclei, including
      dim and marginal ones; HIGHER keeps only confident detections. This is the
      knob for a plate that misses real cells or counts debris.
    * ``nms_thresh`` — overlap threshold in the polygon non-maximum-suppression
      pass. HIGHER lets more overlapping polygons survive, which recovers
      touching nuclei in a confluent field at the risk of double-counting one;
      LOWER merges more aggressively, which removes duplicates but can fuse
      neighbours.
    * ``tile_px`` — peak-memory cap, NOT a detection knob (see below).
    """

    prob_thresh: float | None = None
    nms_thresh: float | None = None
    tile_px: int = 0


DEFAULT_STARDIST_PARAMS = StarDistParams()

# The user-facing explanation of the two detection thresholds, in one place.
#
# Both GUI panels that expose them — the Analyze form and the Review pane's
# Playground — show this text, and both are Qt code that cannot be the source of
# truth for the other. Defined here, beside the dataclass whose docstring
# describes the same two knobs, because this module is the one thing both panels
# already import (for ``parse_threshold``). They previously carried their own
# copies and had already drifted into describing the same numbers differently.
#
# The probability text names the Analyze form's starting value even though the
# Playground shows the same string. That is deliberate: the two panels start from
# different numbers, so a Playground reader needs to know what an untouched run
# would actually use.
#
# Plain strings, no markup: they are set as Qt tooltips, and Qt wraps them.
PROB_THRESH_HELP = (
    "StarDist object-probability threshold (0-1). Candidate nuclei whose "
    "predicted objectness falls below it are discarded before the polygon "
    "overlap pass.\n"
    "• LOWER detects more nuclei — dim, out-of-focus and marginal ones included. "
    "Turn it down when a plate is missing real cells.\n"
    "• HIGHER keeps only confident detections. Turn it up when debris is being "
    "counted as cells.\n"
    "The Analyze form starts at 0.2 — more sensitive than the pretrained "
    "model's own ~0.479. Clear the field to go back to the model's."
)

NMS_THRESH_HELP = (
    "StarDist non-maximum-suppression overlap threshold (0-1), applied to the "
    "predicted nucleus polygons.\n"
    "• HIGHER lets more overlapping nuclei survive — recovers touching nuclei on "
    "a confluent field, at the risk of counting one nucleus twice.\n"
    "• LOWER merges overlapping polygons more aggressively — removes duplicates, "
    "but can fuse genuine neighbours into one.\n"
    "Blank = use the pretrained model's own value (0.3)."
)

# Peak-memory cap for large images. An untiled ``predict_instances`` on a big
# frame (e.g. ~4K×4K) allocates the full-resolution U-Net activations plus the
# (H×W×n_rays) distance array in one shot — several GB per worker — so a handful
# of parallel workers exhaust host RAM (the "each worker eats ~8GB" symptom on
# large microscopy frames). StarDist can process the frame in overlapping tiles
# and stitch the result, which caps the peak at roughly one tile's worth.
#
# ``tile_px`` is OFF by default (``0`` → no tiling, StarDist's native
# behaviour). When set to N > 0, a frame is tiled so no block exceeds N px per
# side; smaller N = less RAM, a bit slower.
#
# The whole params value is installed per worker by
# ``process_microscopy._worker_init`` from the ``--stardist_*`` CLI args (all
# exposed in the Analyze GUI) via :func:`set_params`.
_STARDIST_PARAMS = DEFAULT_STARDIST_PARAMS


def parse_threshold(value) -> float | None:
    """Normalise one StarDist threshold to ``float | None``.

    The single rule shared by the CLI, the Analyze form and the Playground, so
    the three can never disagree about what "auto" means: blank, ``None``,
    unparseable, or negative (the CLI's ``-1`` sentinel) all mean *use the
    model's own default*, and anything else must be a probability in [0, 1].
    A value outside that range is rejected by raising, not clamped — a silently
    clamped threshold would segment with parameters the operator did not choose.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if not text or text == "auto":
            return None
        value = text
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(num):
        # ``float("nan")`` parses, and then fails *every* comparison — it is
        # neither ``< 0`` nor ``> 1.0`` — so without this line it passed both
        # bounds and reached ``predict_instances(prob_thresh=nan)``, where every
        # candidate nucleus compares false against it. A value that cannot be
        # ordered is not a probability; it means the same as blank.
        return None
    if num < 0:
        return None                     # the "-1 = model default" sentinel
    if num > 1.0:
        raise ValueError(f"StarDist threshold must be in [0, 1] (got {num!r}).")
    return num


def set_params(params: StarDistParams | None) -> None:
    """Install the per-worker StarDist parameters (called from ``_worker_init``)."""
    global _STARDIST_PARAMS
    _STARDIST_PARAMS = params or DEFAULT_STARDIST_PARAMS


def get_params() -> StarDistParams:
    """The parameters this worker will segment with."""
    return _STARDIST_PARAMS


def set_tile_px(tile_px: int | None) -> None:
    """Install just the tiling threshold (px per side; 0 = off), leaving the
    detection thresholds as they are."""
    try:
        px = max(0, int(tile_px or 0))
    except (TypeError, ValueError):
        px = 0
    set_params(replace(_STARDIST_PARAMS, tile_px=px))


def _stardist_n_tiles(img: np.ndarray, tile_px: int | None = None) -> tuple[int, int] | None:
    """StarDist ``n_tiles`` for *img*, or None to predict untiled.

    Tiles each spatial axis into ceil(size / tile_px) blocks so no block exceeds
    *tile_px* per side; returns None when the whole frame already fits (both axes
    → 1 tile) or tiling is disabled. *tile_px* defaults to the installed params'.
    """
    if tile_px is None:
        tile_px = _STARDIST_PARAMS.tile_px
    if tile_px <= 0:
        return None
    h, w = int(img.shape[0]), int(img.shape[1])
    ny = max(1, (h + tile_px - 1) // tile_px)
    nx = max(1, (w + tile_px - 1) // tile_px)
    if ny == 1 and nx == 1:
        return None
    return (ny, nx)


def set_model(model) -> None:
    """Install the per-worker StarDist model (called from ``_worker_init``)."""
    global _STARDIST_MODEL
    _STARDIST_MODEL = model


def set_fov_label(name: str) -> None:
    """Record the FOV being segmented, for the diagnostic ``seg-input`` line."""
    global _SEG_FOV_LABEL
    _SEG_FOV_LABEL = name


def quiet_frozen_tf_logging() -> None:
    """Silence two frozen-build-only TensorFlow messages that print as red
    "errors" in the app log but do not affect StarDist inference:

    * "Limited tf.compat.v2.summary API due to missing TensorBoard" — TensorBoard
      is intentionally not bundled (tf.summary is unused for inference); emitted
      when TF is first imported.
    * "AutoGraph could not transform <Model.make_predict_function> ... Unable to
      locate the source code" — a frozen build ships compiled .pyc, not .py, so
      AutoGraph cannot ``inspect.getsource()`` the ``tf.function`` body. It runs
      the function as-is, which is correct here (the predict graph is built from
      ops, not converted Python control flow); emitted at first predict.

    Call BEFORE TF is first imported so the tensorflow logger level is raised in
    time to catch the summary warning. No-op off-frozen so developers running
    from source keep full TF logging. Imports TF only when frozen, so the
    module-import-is-TF-free invariant above is preserved.
    """
    import sys
    if not getattr(sys, "frozen", False):
        return
    logging.getLogger("tensorflow").setLevel(logging.ERROR)
    try:
        import tensorflow as tf
        tf.get_logger().setLevel(logging.ERROR)
        tf.autograph.set_verbosity(0)
    except Exception:
        from util.aw_safe import swallowed
        swallowed("quiet_frozen_tf_logging: TF import/config failed")


def _percentile_normalize(arr, pmin: float = 1.0, pmax: float = 99.8):
    """``csbdeep.utils.normalize(arr, pmin, pmax, clip=True)``, or its equal.

    csbdeep is imported where it exists — a real run always has it, since
    StarDist depends on it — and the numpy form below runs only where it does
    not, which in practice is the test environment and a GUI build that has
    never loaded TensorFlow.

    The fallback is **measured** equivalent rather than assumed so: over random
    frames at scales from 1e-3 to 65535, and over a constant frame, the largest
    absolute difference between the two was 1.19e-07 — exactly float32 epsilon,
    i.e. the same function evaluated twice. That measurement is the only reason
    a fallback is acceptable here at all; a normalisation that quietly differed
    would make the preview and the pipeline disagree about the same frame.
    """
    try:
        from csbdeep.utils import normalize
    except ImportError:
        # ``np.percentile`` returns a float64 scalar. Under numpy 1.x's
        # value-based casting that still leaves a float32 array; under NEP 50
        # (numpy 2) it would promote the whole expression to float64 and this
        # fallback would silently start returning a different dtype from
        # csbdeep's ``normalize``, which casts its own bounds to the target
        # dtype first. The app pins numpy 1.24, so this is latent — and it is
        # exactly the kind of divergence a dependency bump introduces without
        # touching a line of this file, so the casts are explicit.
        lo = np.float32(np.percentile(arr, pmin))
        hi = np.float32(np.percentile(arr, pmax))
        out = (arr.astype(np.float32) - lo) / (hi - lo + np.float32(1e-20))
        return np.clip(out, 0.0, 1.0).astype(np.float32)
    return normalize(arr, pmin, pmax, clip=True)


def prepare_stardist_input(image) -> "np.ndarray":
    """Scrub, normalise and clamp *image* into the distribution StarDist expects.

    **Every caller that hands an array to the network must go through this**,
    and it is a shared function rather than three lines inlined because the
    second caller got it wrong the first time. The Playground's threshold sweep
    resolved instances straight off ``model.predict(frame)`` on the corrected
    frame — raw camera counts — and measured 6 nuclei falling to 4 across the
    whole threshold range on a frame where the truth is 86 falling to 0. It did
    not fail: it drew a flat line, and the manual tells the reader a flat line
    means the threshold does not matter. The opposite of the truth, confidently,
    with nothing marking the shortfall.

    ``StarDist2D.predict`` takes ``normalizer=None`` and its own docstring says
    that assumes the image is already normalised. Nothing enforces it.

    The clamp is not tidiness either. StarDist can hit an **uncatchable C++
    ClipperLib abort** — a ``terminate()``, not an exception — when fed
    non-finite or out-of-distribution input, and in the GUI process that takes
    the whole application down rather than one worker. Two ways in: a single
    NaN or Inf pixel, since ``normalize`` uses ``np.percentile`` rather than the
    nan-aware variant and one NaN turns every percentile NaN; and huge *finite*
    values, since an empty-well flat-field divides by a near-zero field and
    amplifies a dim region by ~100x while ``normalize(clip=False)`` does not cut
    the top end. So: scrub non-finite, normalise **with** ``clip=True``, then
    clamp explicitly, leaving the input strictly inside
    :data:`_STARDIST_INPUT_CLIP`.
    """
    arr = np.asarray(image, dtype=np.float32)
    if not np.all(np.isfinite(arr)):
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    out = _percentile_normalize(arr)
    if not np.all(np.isfinite(out)):
        out = np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
    np.clip(out, _STARDIST_INPUT_CLIP[0], _STARDIST_INPUT_CLIP[1], out=out)
    return out


def is_near_constant(image) -> bool:
    """Whether *image* has effectively no dynamic range.

    A near-constant field has no nuclei and is the classic ClipperLib trigger,
    so every caller skips prediction on one rather than risking the abort.
    Shared for the same reason the prelude is: the sweep runs nineteen NMS
    passes per press, so it has nineteen times the exposure of one Segment
    press, and it had none of the protection.
    """
    arr = np.asarray(image)
    if arr.size == 0:
        return True
    return float(arr.max()) - float(arr.min()) < 1e-6


def segment_nuclei(nir_corr: np.ndarray, *, model,
                   params: StarDistParams = DEFAULT_STARDIST_PARAMS) -> np.ndarray:
    """Segment nuclei in one corrected frame with an explicit model + params.

    The whole of the pipeline's StarDist inference, with nothing read from
    module state — so an interactive caller (the Review pane's Playground) runs
    *this* function and therefore gets exactly what a batch run would produce,
    including the input scrubbing and the near-constant-field refusal below.
    ``_segment_stardist_nuclei`` is the worker-global-driven wrapper over it.
    """
    if model is None:
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
    nir_norm = prepare_stardist_input(nir_corr)

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
    if is_near_constant(nir_norm):
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
            pred = model.predict(nir_norm)
            prob, dist = pred[0], pred[1]
            log.info("  seg-debug %s: prob[min=%.3g max=%.3g] dist[min=%.3g "
                     "max=%.3g] dist_nonfinite=%d prob_shape=%s dist_shape=%s",
                     fov, float(np.nanmin(prob)), float(np.nanmax(prob)),
                     float(np.nanmin(dist)), float(np.nanmax(dist)),
                     int((~np.isfinite(dist)).sum()),
                     tuple(np.shape(prob)), tuple(np.shape(dist)))
        except Exception as exc:
            log.warning("  seg-debug %s: predict() trace failed: %s", fov, exc)

    _t0 = time.perf_counter()
    # Tile large frames to cap peak memory (see StarDistParams.tile_px).
    # n_tiles=None → StarDist's own default (untiled) for small frames.
    #
    # A threshold left at None is OMITTED rather than passed: predict_instances
    # treats None as "fall back to the model's own thresholds.json", so the two
    # are equivalent, but omitting it keeps the default call identical to the
    # one this function made before the thresholds were tunable.
    kwargs: dict = {}
    n_tiles = _stardist_n_tiles(nir_norm, params.tile_px)
    if n_tiles is not None:
        kwargs["n_tiles"] = n_tiles
    if params.prob_thresh is not None:
        kwargs["prob_thresh"] = float(params.prob_thresh)
    if params.nms_thresh is not None:
        kwargs["nms_thresh"] = float(params.nms_thresh)
    labels, _ = model.predict_instances(nir_norm, **kwargs)
    # Per-FOV inference timing: a slow per-call number points at CPU contention /
    # thread starvation (vs the run merely doing more FOVs). Pairs with the
    # ``seg-input`` line above so each FOV's input stats + predict cost sit
    # together in the log. n_tiles and the thresholds are logged so a memory /
    # speed tradeoff and the detection settings in force are both visible — a
    # nucleus count only means something next to the thresholds that produced it.
    log.info("  seg %s: predict_instances %.1fs (%d nuclei, n_tiles=%s, "
             "prob=%s, nms=%s)",
             fov, time.perf_counter() - _t0,
             int(labels.max()) if labels.size else 0, n_tiles or "1",
             "model" if params.prob_thresh is None else f"{params.prob_thresh:g}",
             "model" if params.nms_thresh is None else f"{params.nms_thresh:g}")
    return labels.astype(np.int32)


def _segment_stardist_nuclei(nir_corr: np.ndarray) -> np.ndarray:
    """Segment nuclei with the model and parameters installed in this worker."""
    return segment_nuclei(nir_corr, model=_STARDIST_MODEL, params=_STARDIST_PARAMS)


def _label_areas(labels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(ids, areas)`` for every non-zero label in *labels*, ids ascending.

    ``bincount`` is the fast path and allocates one slot per integer up to the
    largest id, so it is used only where that is bounded by the image itself —
    a segmentation numbers its objects ``1..N`` with ``N`` at most the pixel
    count. A label image that breaks either assumption (a negative value, or an
    id larger than the array) falls back to ``np.unique``, which costs a sort
    but cannot be made to allocate a gigabyte by one stray id. A **non-integer**
    label image takes that fallback too: ``bincount`` would have to truncate
    towards zero to index with it, which silently merges ``2.0`` and ``2.5``
    into one object. The segmenter's own labels are ``int32``, so this is a
    guard rather than a live path — but the merge would be invisible if it ever
    became one.
    """
    flat = labels.ravel()
    integral = flat.dtype.kind in "iub"
    lmax = int(flat.max()) if (flat.size and integral) else 0
    lmin = int(flat.min()) if (flat.size and integral) else 0
    if integral and lmin >= 0 and lmax <= flat.size:
        counts = np.bincount(flat.astype(np.intp, copy=False), minlength=1)
        ids = np.flatnonzero(counts)
        ids = ids[ids != 0]
        return ids, counts[ids]
    ids, counts = np.unique(flat, return_counts=True)
    keep = ids != 0
    return ids[keep], counts[keep]


def nucleus_seed_markers(
    nucleus_labels: np.ndarray, min_nucleus_area_px: int
) -> tuple[np.ndarray, int, int]:
    """One single-pixel watershed seed per nucleus large enough to keep.

    Returns ``(markers, n_markers, n_below_min_area)``: a label image carrying
    marker ids ``1..n_markers`` at the rounded centroid of each surviving
    nucleus, how many seeds were planted, and how many nuclei were rejected for
    being smaller than *min_nucleus_area_px*.

    Whole-array work, not a per-nucleus loop. The loop this replaces asked
    ``np.count_nonzero(labels == nid)`` for the area and
    ``ndi.center_of_mass(labels == nid)`` for the centroid, which is one and two
    full passes over the label image *per nucleus*: on 1500 nuclei in a 2048²
    frame that measured 1.43 s for the areas and 23.67 s for the centroids,
    against 0.076 s and 0.280 s for the two calls here — roughly 25 s of every
    FOV, invisible in the per-FOV ``seg`` timing line, which times only
    ``predict_instances``.

    The arithmetic is the same arithmetic. ``bincount`` counts each label's
    pixels in one pass, and ``center_of_mass`` over a constant input restricted
    to *index* is the mean coordinate of each label's pixels — which is what the
    boolean-mask call computed one nucleus at a time. Two nuclei whose rounded
    centroids land on the same pixel still leave one marker there and one id
    unused, exactly as the loop's overwrite did: NumPy's indexed assignment
    keeps the last write.
    """
    labels = np.asarray(nucleus_labels)
    if labels.size == 0:
        return np.zeros_like(labels, dtype=np.int32), 0, 0
    ids, areas = _label_areas(labels)
    n_total = int(ids.size)
    keep = ids[areas >= int(min_nucleus_area_px)]
    markers = np.zeros(labels.shape, dtype=np.int32)
    if keep.size == 0:
        return markers, 0, n_total

    centres = np.asarray(
        ndi.center_of_mass(np.ones(labels.shape, dtype=np.uint8), labels, keep),
        dtype=float,
    ).reshape(-1, 2)
    finite = np.isfinite(centres).all(axis=1)
    centres = centres[finite]
    if centres.size == 0:
        return markers, 0, n_total - int(keep.size)

    ys = np.clip(np.rint(centres[:, 0]).astype(np.int64), 0, labels.shape[0] - 1)
    xs = np.clip(np.rint(centres[:, 1]).astype(np.int64), 0, labels.shape[1] - 1)
    n_markers = int(ys.size)
    markers[ys, xs] = np.arange(1, n_markers + 1, dtype=np.int32)
    return markers, n_markers, n_total - int(keep.size)


def cytoplasm_mask(cytoplasm_tophat: np.ndarray, fov: str) -> np.ndarray:
    """Otsu mask of the corrected cytoplasm frame, saying so when it is empty.

    A constant cytoplasm frame yields no pixels at all, because a threshold on
    a constant image cannot be strictly exceeded by any of its pixels. That is a
    legitimate outcome — an empty or saturated cytoplasm channel has no
    cytoplasm to find — but it produces zero cells for the whole FOV, and it
    used to produce them in silence, forty lines below the near-constant
    *nuclear* input that this module refuses with an explicit warning. Both
    halves of one function now say when they could not measure something.

    The constant case is **tested for**, not caught. ``threshold_otsu`` was
    assumed to raise on one, and with the pinned skimage (0.24.0) it does not:
    it returns the constant value, so the ``except`` never fired and the
    constant warning was unreachable while four artefacts described it as the
    behaviour. An empty frame is the case that does raise, and it raises
    ``IndexError`` rather than ``ValueError``, so the handler could not see that
    one either.
    """
    if cytoplasm_tophat.size == 0:
        thresh = 0.0
        log.warning(
            "  seg %s: the corrected cytoplasm frame is empty, so there is "
            "nothing to threshold. This FOV will yield 0 cells.", fov,
        )
    elif float(cytoplasm_tophat.min()) == float(cytoplasm_tophat.max()):
        thresh = float(cytoplasm_tophat.reshape(-1)[0])
        log.warning(
            "  seg %s: the cytoplasm channel is constant (every pixel %.4g), so "
            "there is no split to find — no pixel is strictly above the "
            "threshold. This FOV will yield 0 cells.", fov, thresh,
        )
    else:
        try:
            thresh = float(threshold_otsu(cytoplasm_tophat))
        except (ValueError, IndexError):
            thresh = float(np.mean(cytoplasm_tophat))
            log.warning(
                "  seg %s: Otsu could not split the cytoplasm channel — falling "
                "back to its mean (%.4g).", fov, thresh,
            )
    mask = cytoplasm_tophat > thresh
    if not mask.any():
        log.warning(
            "  seg %s: the cytoplasm mask is empty at threshold %.4g — no pixel "
            "of the corrected cytoplasm frame is above it, so the watershed has "
            "nothing to fill and this FOV will yield 0 cells.", fov, thresh,
        )
    return mask


def _segment_stardist_seeded_watershed_cell(
    nir_corr: np.ndarray,
    cytoplasm_raw: np.ndarray,
    tophat_radius_fluor: int,
    min_nucleus_area_px: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    fov = _SEG_FOV_LABEL or "?"
    stardist_labels = _segment_stardist_nuclei(nir_corr)
    nuclear_points, n_markers, n_small = nucleus_seed_markers(
        stardist_labels, min_nucleus_area_px)
    if n_markers == 0:
        log.warning(
            "  seg %s: no watershed seeds — %d nucleus/nuclei were detected and "
            "all of them are smaller than min_nucleus_area_px=%d, so this FOV "
            "will yield 0 cells.", fov, n_small, int(min_nucleus_area_px),
        )
    elif n_small:
        log.info("  seg %s: %d nucleus/nuclei below min_nucleus_area_px=%d "
                 "were not seeded (%d seed(s) planted).",
                 fov, n_small, int(min_nucleus_area_px), n_markers)

    # Isotropic disk top-hat (not the square apply_tophat): a square footprint
    # here stamps square bias onto the cytoplasm mask → square-edged watershed
    # cells, regardless of the chosen --background method.
    cytoplasm_tophat = _isotropic_disk_tophat(cytoplasm_raw, tophat_radius_fluor)
    cytoplasm_binary_mask = cytoplasm_mask(cytoplasm_tophat, fov)
    # A seed outside the mask is dropped by ``watershed(mask=…)`` without a word,
    # so a frame whose cytoplasm stain does not cover its nuclei loses cells one
    # at a time and reports the shortfall nowhere. Count them here.
    outside = int(np.count_nonzero((nuclear_points > 0) & ~cytoplasm_binary_mask))
    if outside:
        log.warning(
            "  seg %s: %d of %d watershed seed(s) fall outside the cytoplasm "
            "mask and are dropped — those nuclei produce no cell.",
            fov, outside, n_markers,
        )
    distance = ndi.distance_transform_edt(cytoplasm_binary_mask)
    if n_markers == 0:
        labels = np.zeros_like(stardist_labels, dtype=np.int32)
    else:
        labels = watershed(
            -distance,
            markers=nuclear_points,
            mask=cytoplasm_binary_mask,
        ).astype(np.int32)
    return labels, nuclear_points, cytoplasm_tophat, cytoplasm_binary_mask
