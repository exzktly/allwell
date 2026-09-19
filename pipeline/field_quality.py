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

import numpy as np
from scipy.ndimage import sobel

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
ACQUISITION_METRICS = ("focus", "sat_frac", "dyn_range")

#: Metrics that describe **what was in the well**. A cytotoxic compound drops
#: nucleus count ten- to twentyfold, and cell-cycle arrest enlarges nuclei;
#: scored against the plate median these would flag the wells where the
#: experiment worked. They are recorded and never scored plate-wide —
#: see ``review/domain/field_quality_scores.py``.
BIOLOGICAL_METRICS = ("nuc_count", "nuc_area_median", "bg_level")


def focus_score(image) -> float | None:
    """Normalised Tenengrad: gradient energy over image variance.

    Invariant to gain and to an additive pedestal, and near-flat in cell
    count — the module docstring carries the measurements. Higher is sharper.

    ``None`` for a frame with no variance at all (a blank or saturated-flat
    field), because the ratio is undefined there and zero is a measurement.
    """
    arr = np.asarray(image, dtype=np.float64)
    if arr.size == 0:
        return None
    finite = np.isfinite(arr)
    if not finite.all():
        arr = np.where(finite, arr, 0.0)
    var = float(np.var(arr))
    if var <= 0.0:
        return None
    gx = sobel(arr, axis=0)
    gy = sobel(arr, axis=1)
    return float(np.mean(gx * gx + gy * gy) / var)


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
                  areas=None) -> dict:
    """Every metric for one field, as a plain dict keyed by :data:`METRIC_KEYS`.

    *nuclear_image* is the frame the focus and spread metrics are read from —
    the caller decides whether that is pre- or post-correction and records which,
    because flat-field division rewrites the array in place
    (``process_microscopy._correct_image_group``) and changes local variance
    spatially, which is exactly what a focus metric reads.

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
        "focus": focus_score(nuclear_image),
        "sat_frac": saturated_fraction(nuclear_image, dtype_max=dtype_max),
        "dyn_range": dynamic_range(nuclear_image),
        "bg_level": background_level(nuclear_image, lab),
        "nuc_count": count,
        "nuc_area_median": median_area,
    }
