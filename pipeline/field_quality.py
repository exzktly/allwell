"""Per-field image-quality metrics: what a run can say about the pictures it read.

``pipeline/run_report.py`` exists because a run that lost FOV-groups still wrote
a per-well CSV and ``<well>_out.zip``, and on disk that output was
indistinguishable from a well that processed cleanly. That fixed **coverage**.
Nothing fixed **quality**. A field that was out of focus, saturated, empty or
covered in debris still yields nuclei, still yields intensities, and lands in
the CSV looking exactly like a good one — and the per-cell QC gate
(``review/domain/qc_params.py``) reads shape, so it cannot see that a whole
field was unusable.

Nothing in this tree measured focus, blur or saturation before this module.
``pipeline/stardist.py`` computes field statistics per FOV — min, max, mean,
dynamic range, finite and non-zero fractions — and puts them in a log line, so
that the last line before an uncatchable ClipperLib abort names the culprit.
They are not persisted, so no consumer downstream can read them.

Stdlib + numpy + ``scipy.ndimage`` only, Qt-free and free of ``review`` /
``widgets``, like every ``pipeline`` module: the remote worker imports it, and
``services/remote_manifest.py`` ships it.

The focus statistic, and why it is not the obvious one
------------------------------------------------------

The obvious choice is variance of the Laplacian, normalised by mean squared to
make it exposure-invariant. **Measured, it is neither exposure-invariant nor a
focus metric.** On 512x512 synthetic frames (Gaussian nuclei at sigma 1.2,
Poisson shot noise, a 100-count sCMOS pedestal):

======================================  ===============================
condition                               ``var(laplace(I)) / mean(I)**2``
======================================  ===============================
gain varied 16x, identical biology      **30x spread**
pedestal 0 -> 1000, identical biology   **707x spread**
sharp, cell count 20 -> 600             **6.7x spread**
======================================  ===============================

It fails for two reasons that are always present. Shot-noise variance scales
with the gain ``a``, not with ``a**2``, and the Laplacian amplifies it; and an
additive pedestal ``b`` leaves the numerator at ``a**2 V`` while the denominator
becomes ``(a*mu + b)**2``. Worse than either, the statistic tracks how many
cells are in the field: across a 30-fold range of cell count, in perfect focus
throughout, it spans 6.7x. On a plate where a compound legitimately kills cells,
a focus metric that moves 6.7x with cell count would report the treated wells as
out of focus.

**Normalised Tenengrad** — gradient energy over image variance — has neither
problem. Gain cancels because numerator and denominator both scale as ``a**2``;
the pedestal cancels because both are built from differences. Same frames, same
table:

=====================================  ==================
condition                              ``tenengrad / var``
=====================================  ==================
gain 0.25 / 1 / 4                      27.34 / 27.47 / 27.48
pedestal 0 / 100 / 1000                27.48 / 27.47 / 27.43
defocus sigma 0 / 1 / 2 / 4            27.47 / 19.60 / 10.43 / 4.19
sharp, cell count 20 -> 600            27.37 .. 27.55
=====================================  ==================

Flat to **1.4 %** across a thirty-fold cell-count range, falling **6.6x** under
defocus, and across three seeds at five cell counts the sharp frames
(27.27-27.64) and the defocused ones (10.16-11.84) do not overlap at all.

That is a synthetic measurement and it settles the *form* of the statistic, not
its threshold. Whether the separation survives real optics, real stains and real
plates is the open question, and it is why nothing here decides what counts as
bad: this module measures, and the Review side scores.
"""

from __future__ import annotations

import math

import numpy as np

# The one "merge a named block into the sidecar" writer. Borrowed rather than
# re-implemented: a second atomic merge would be a second set of rules about
# what happens when the file is missing or malformed, and the two would
# diverge on the first bug fixed in only one of them. It lives in
# ``run_report`` because that block was the first to need it; that is a
# historical fact about the sidecar, not a layer this module sits above.

#: Keys a field record carries. Named once so the writer, the reader and the
#: scorer cannot drift — a metric added to one and not the others is invisible
#: exactly the way a missing roster entry is.
METRIC_KEYS = (
    "focus",
    "sat_frac",
    "dyn_range",
    "bg_level",
    "nuc_count",
    "nuc_area_median",
)

#: Metrics that describe how the picture was **taken**. These are expected to be
#: uniform across a plate by design, so a field far from the plate's own median
#: is evidence of an acquisition fault.
#:
#: ``focus`` stays here now that it is absolutely comparable, and the two
#: readings are complementary rather than redundant. The absolute cut (Review's
#: per-cell focus gate) answers "is this soft?"; the plate-wide robust z here
#: answers "is this field unlike its plate?", which catches a field that is
#: *worse than its neighbours* while still above anyone's threshold — and which
#: needs no threshold to be chosen at all, so it works on a plate whose user
#: has not set one. What the z cannot see is a plate that is uniformly soft;
#: that is exactly what the absolute cut is for, and why both exist.
ACQUISITION_METRICS = ("focus", "sat_frac", "dyn_range")

#: Metrics that describe **what was in the well**. A cytotoxic compound drops
#: nucleus count ten- to twentyfold, and cell-cycle arrest enlarges nuclei;
#: scored against the plate median these would flag the wells where the
#: experiment worked. They are recorded and never scored plate-wide —
#: see ``review/domain/field_quality_scores.py``.
BIOLOGICAL_METRICS = ("nuc_count", "nuc_area_median", "bg_level")


#: Widest ring, in pixels, that the focus estimator reads around an outline.
#: Swept 1-4 on nuclei of radius 8: separation improves up to 3 and flattens
#: after.
EDGE_BAND_PX = 3

#: How much of a nucleus's own radius the ring may take. A fixed 3 px band
#: needs an interior left after eroding by ``w + 2``, so every nucleus under
#: about 6 px radius returned ``None`` — measured, radius 4 and 6 were
#: *entirely* unmeasurable while radius 8 and up were fine. That is a whole
#: class of plate (small nuclei, low magnification, a binned camera) silently
#: carrying no focus measurement at all, so the ring scales with the object
#: and only falls back to the widest value where the nucleus can afford it.
EDGE_BAND_RADIUS_FRAC = 0.34

#: Where in the ring the estimator reads. The edge's response is a *peak*, and
#: a mean over the ring dilutes it with pixels that are not on the transition —
#: measured, the mean form separates at 1.60x where the 95th percentile
#: separates at 2.11x on the same fixtures.
EDGE_PEAK_PCT = 95.0


def edge_focus(image, mask) -> float | None:
    """How sharp one nucleus's own outline is. Higher is sharper.

    **This is the per-cell focus measurement, and every part of it was chosen
    by measurement against the alternatives.** The module docstring's table
    carries the sweep; the short version:

    ``sqrt(max(0, peak|∇²I|_edge² − peak|∇²I|_background²)) / contrast``

    * **The Laplacian, not the gradient.** At a step edge blurred by a Gaussian
      of width σ the peak gradient scales as ``1/σ`` while the peak Laplacian
      scales as ``1/σ²`` — twice as sensitive in log terms. Measured over
      σ 0→3: the gradient form spans 2.85×, this one 9.63×.
    * **At the outline, not over the cell or the frame.** Defocus is a property
      of the *optics*: it sets the width of the intensity transition at an
      edge, and that width is the same whatever the object's size. This is what
      removes the confounder that disqualifies every whole-image statistic
      here — measured on *this* function, a fourfold nucleus-size range
      (radius 4 to 16 px) spans **1.36×**, against **7.8×** for the
      frame-level Tenengrad.
    * **Divided by the cell's own contrast** (median inside minus median in a
      ring outside), which removes gain and an additive pedestal exactly.
    * **The background ring's own Laplacian subtracted in quadrature.** Noise
      adds Laplacian energy, so a dim noisy cell reads as *sharper* than it is —
      the false-negative direction, and the same defect that disqualified
      ``var(laplace)/mean²`` at frame scale. The ring is already being read for
      the contrast denominator, so the floor costs one extra percentile.
      Without it this inverts above σ≈3, giving two very different focus states
      one number; with it, monotone through σ≈3 and the drift over a
      fifteenfold noise range falls from 1.86× to 1.31×.

    **Absolutely comparable between cells, fields, wells and plates**, which is
    the property the whole layer rests on: a score relative to its own frame
    cannot see a frame that is uniformly soft, where every cell deviates from
    its neighbours by nothing.

    Measured on this function rather than on the probe that chose it: over
    σ 0→3 the per-field median runs 0.3345, 0.2731, 0.1674, 0.1072, 0.0674,
    0.0452 — monotone, spanning **7.4×**. Sharp fields and σ=2 fields separate
    **2.71×** with nucleus radius varied from 4 to 16 px, and every nucleus at
    or above the pipeline's own ``min_nucleus_area_px`` of 50 is measurable.

    **It is not a severity scale past σ≈3.** Beyond that the outline has spread
    wider than the ring that reads it and the background ring starts picking up
    neighbours, so the value flattens and can come back a hair — 0.0467 against
    0.0452 on a 256 px field, though not on a 320 px one. The bound is
    acceptable because severe defocus is also where segmentation fails, so
    there is frequently no outline left to read; it is recorded so nobody reads
    the column as a ranking at the very soft end.

    ``None`` — never ``0.0`` — when the outline cannot be measured: no contrast
    across it, or too few pixels inside or outside to characterise either. A
    cell that could not be measured and a cell measured and found soft are
    different facts (CLAUDE.md §7).
    """
    from scipy.ndimage import binary_dilation, binary_erosion, laplace

    arr = np.asarray(image, dtype=np.float64)
    m = np.asarray(mask, dtype=bool)
    if arr.shape != m.shape or not m.any():
        return None
    finite = np.isfinite(arr)
    if not finite.all():
        arr = np.where(finite, arr, 0.0)

    w = _edge_band_for(int(m.sum()))
    grown = binary_dilation(m, iterations=w)
    band = grown & ~binary_erosion(m, iterations=w)
    # One pixel clear of the ring on each side, not a fixed two and four: at
    # the narrowest ring those offsets ate the whole interior of a nucleus at
    # the smallest area the pipeline accepts (``min_nucleus_area_px`` 50, about
    # 4 px radius), so it reported ``None`` for every cell on such a plate.
    deep = binary_erosion(m, iterations=w + 1)
    far = binary_dilation(m, iterations=w + 3) & ~grown
    if band.sum() == 0 or deep.sum() < _EDGE_MIN_PX or far.sum() < _EDGE_MIN_PX:
        return None

    contrast = float(np.median(arr[deep]) - np.median(arr[far]))
    if contrast <= 0.0:
        # A nucleus dimmer than its surroundings is not a focus measurement —
        # it is a mask that does not describe this image.
        return None

    lap = np.abs(laplace(arr))
    edge = float(np.percentile(lap[band], EDGE_PEAK_PCT))
    floor = float(np.percentile(lap[far], EDGE_PEAK_PCT))
    signal = edge * edge - floor * floor
    if signal <= 0.0:
        # The edge is indistinguishable from the noise in the background. Not
        # zero sharpness — no measurement.
        return None
    return float(np.sqrt(signal) / contrast)


#: Fewest interior (or exterior) pixels an outline needs before its contrast
#: and noise floor mean anything.
_EDGE_MIN_PX = 10


def _edge_band_for(area_px: int) -> int:
    """Ring half-width for a nucleus of *area_px*, at least 1 and at most
    :data:`EDGE_BAND_PX`.

    Taken from the equivalent radius rather than from the area directly, so
    the ring is a fixed fraction of the distance from the outline to the
    centre however the nucleus is shaped.
    """
    if area_px <= 0:
        return 1
    radius = math.sqrt(area_px / math.pi)
    return int(max(1, min(EDGE_BAND_PX, round(radius * EDGE_BAND_RADIUS_FRAC))))


def field_focus(cell_values) -> float | None:
    """One field's focus, as the median of its cells' own outlines.

    **This replaces a whole-frame statistic that could not work.** The first
    implementation was a normalised Tenengrad over the entire frame, and two
    measurements retired it:

    * **Nucleus size moved it more than defocus did.** Over a fourfold radius
      range on sharp frames it spanned **7.8×**, while the whole defocus signal
      from σ 0 to 3 spanned **3.7×** — a confounder twice the size of the
      signal, with sharp and blurred frames overlapping completely. A plate
      with two cell lines, or one where confluence changes nuclear size between
      conditions, would have read the difference as focus.
    * **It barely noticed the case the layer is for.** A frame with half of it
      blurred at σ=2 moved it only 14 % (27.44 → 23.59), against a flag that
      fires at five MADs from the plate median.

    Aggregating :func:`edge_focus` fixes both, because each cell's outline is a
    property of the optics rather than of the cell. The median rather than the
    mean, so a handful of mis-segmented outlines cannot drag a field's number.

    ``None`` when no cell in the field could be measured — which is itself the
    honest reading of a frame too soft to segment.
    """
    vals = [float(v) for v in (cell_values or ()) if v is not None]
    if not vals:
        return None
    return float(np.median(np.asarray(vals, dtype=np.float64)))


def cell_focus_by_label(image, labels) -> dict[int, float | None]:
    """:func:`edge_focus` for every label, as ``{label_id: value | None}``.

    One pass per label over a padded crop rather than over the whole frame:
    measured at 323 ms for 400 nuclei on a 2048² frame, against seconds for
    the segmentation of the same frame.
    """
    from scipy.ndimage import find_objects

    lab = np.asarray(labels)
    arr = np.asarray(image)
    out: dict[int, float | None] = {}
    if lab.shape != arr.shape or lab.size == 0:
        return out
    pad = EDGE_BAND_PX + 6  # the widest ring plus its outer background ring
    for i, sl in enumerate(find_objects(lab), start=1):
        if sl is None:
            continue
        box = tuple(slice(max(0, s.start - pad), s.stop + pad) for s in sl)
        out[i] = edge_focus(arr[box], lab[box] == i)
    return out


def saturated_fraction(image, *, dtype_max: float | None) -> float | None:
    """Fraction of pixels at the sensor's maximum, or ``None`` when unknowable.

    **This is only computable before the array becomes float.**
    ``pipeline.io.load_2d_gray`` casts every input to ``float32``
    unconditionally, so by the time a frame reaches quantification the camera
    dtype is gone and "at the dtype maximum" would be a comparison against
    ``3.4e38`` — identically zero for every field of every plate, for ever. A
    number stored where nothing was computed is the defect this whole subsystem
    exists to prevent, so the caller passes the maximum it read the file at, and
    ``None`` here means nobody could say.
    """
    if dtype_max is None:
        return None
    arr = np.asarray(image)
    if arr.size == 0:
        return None
    return float(np.count_nonzero(arr >= dtype_max) / arr.size)


def dynamic_range(image, *, lo_pct: float = 0.1, hi_pct: float = 99.9) -> float | None:
    """Robust spread of the frame: the ``hi_pct``-``lo_pct`` percentile gap.

    Percentiles rather than ``max - min``, which one hot pixel decides.

    Not to be confused with the ``dyn_range`` in ``pipeline.stardist``'s
    ``seg-input`` log line: that one is computed on the percentile-normalised,
    ``[0, 1]``-clipped array StarDist is fed, so it is ~1.0 for every
    non-degenerate frame and says nothing about the picture.
    """
    arr = np.asarray(image, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    lo, hi = np.percentile(arr, [lo_pct, hi_pct])
    return float(hi - lo)


def background_level(image, labels=None) -> float | None:
    """Median of the pixels **outside** the nuclei, or of the frame with none.

    A field whose background did not subtract is measurable here and nowhere
    else. Biological rather than acquisition, because treatment-induced
    autofluorescence raises it legitimately.
    """
    arr = np.asarray(image, dtype=np.float64)
    if arr.size == 0:
        return None
    if labels is not None:
        lab = np.asarray(labels)
        if lab.shape == arr.shape:
            arr = arr[lab == 0]
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    return float(np.median(arr))


def field_metrics(nuclear_image, *, labels=None, dtype_max: float | None = None,
                  areas=None, cell_focus=None) -> dict:
    """Every metric for one field, as a plain dict keyed by :data:`METRIC_KEYS`.

    *nuclear_image* is the frame the spread and background metrics are read
    from — the caller decides whether that is pre- or post-correction and
    records which, because flat-field division rewrites the array in place
    (``process_microscopy._correct_image_group``) and rescales it spatially.

    *cell_focus* is the field's per-cell :func:`edge_focus` values; ``focus``
    is their median. It is not derived from *nuclear_image* at all, because
    every whole-image focus statistic tried here confounded nucleus size with
    defocus by more than the defocus signal itself — see :func:`field_focus`.

    Every value may be ``None``. That is the point: a metric that could not be
    computed is absent, never zero.
    """
    lab = None if labels is None else np.asarray(labels)
    count = int(lab.max()) if lab is not None and lab.size else None
    if areas is not None and len(areas):
        median_area = float(np.median(np.asarray(areas, dtype=np.float64)))
    else:
        median_area = None
    return {
        "focus": field_focus(cell_focus),
        "sat_frac": saturated_fraction(nuclear_image, dtype_max=dtype_max),
        "dyn_range": dynamic_range(nuclear_image),
        "bg_level": background_level(nuclear_image, lab),
        "nuc_count": count,
        "nuc_area_median": median_area,
    }


def group_record(*, acquisition: dict, labels, corrected_image, areas,
                 cell_focus=None, fov: str = "", timepoint: str = "") -> dict:
    """One FOV-group's quality record: the pre-correction half plus the rest.

    *acquisition* is what the loader measured **before** anything rewrote the
    array — saturation and dynamic range, in that state for the reasons
    :func:`saturated_fraction` and the module docstring give. Focus is **not**
    among them: it is the median of *cell_focus*, each read at a nucleus's own
    outline after segmentation, because every whole-image focus statistic
    confounded nucleus size with defocus by more than the signal
    (:func:`field_focus`). The three
    biological metrics are measured here because they need the segmentation:
    the nucleus count and the median nuclear area come from *labels*, and the
    background level is the median of the pixels **outside** them on
    *corrected_image*, which is the one place a background that failed to
    subtract is visible at all.

    The split is not cosmetic. :mod:`review.domain.field_quality_scores` scores
    the acquisition three against the whole plate and the biological three only
    within a well, because a treatment that kills cells legitimately moves the
    count and a plate-wide z on it would flag the biology as an artifact.

    *fov* and *timepoint* address the record, and come from the caller because
    the filename parse lives in ``process_microscopy``. Both may be ``""`` — a
    plate with no FOV slot in its schema still has one field per well, and an
    empty address is honest where an invented ``1`` is not.
    """
    lab = None if labels is None else np.asarray(labels)
    vals = list(areas or ())
    record = {
        "fov": str(fov or ""),
        "timepoint": str(timepoint or ""),
        "nuc_count": (int(lab.max()) if lab is not None and lab.size else None),
        "nuc_area_median": (float(np.median(np.asarray(vals, dtype=np.float64)))
                            if vals else None),
        "bg_level": background_level(corrected_image, lab),
        # Not measured from the image: the median of what each nucleus's own
        # outline reported. See ``field_focus`` for the two measurements that
        # retired the whole-frame statistic this replaces.
        "focus": field_focus(cell_focus),
    }
    record.update(acquisition)
    return record


# ── the record on disk ───────────────────────────────────────────────────────
#
# Re-exported from ``pipeline.field_quality_record``, which is stdlib-only so
# the remote client can merge this block across a fetch without pulling numpy
# and scipy in behind it. Everything above measures pixels; everything below
# is bookkeeping about what was measured, and the two halves have different
# dependency budgets.
from pipeline.field_quality_record import (  # noqa: E402,F401
    FIELD_QUALITY_BLOCK,
    FOCUS_COLUMN,
    FIELD_QUALITY_DIRNAME,
    clear_field_quality,
    collect_field_quality,
    finalize_field_quality,
    merge_fetched_field_quality,
    read_field_quality_block,
    records_for_scoring,
    summarize,
    well_is_partial,
    write_well_field_quality,
)
