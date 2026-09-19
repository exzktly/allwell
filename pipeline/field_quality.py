"""Image-quality metrics: what a run can say about the pictures it read.

Six numbers per FOV-group, and — for focus alone — one number per **cell**.
The per-cell one is the column ``qc_edge_focus`` in every well CSV; the
field's own ``focus`` is the median of it. Everything else here is per field.

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

**And it is not what this module computes either.** That table measures cell
*count*, and the axis it never measured is nucleus **size** — where the same
statistic moves **7.8x** over a fourfold radius range against a defocus signal
of only **3.7x**, with the two ranges completely overlapping. A metric that
moves with nuclear size reports a cell line with large nuclei, and the wells
where a cytotoxic compound arrested the cycle, as out of focus. It is the same
defect as the density one above, one variable along, and no amount of
normalising a whole-image statistic removes it: every such statistic is a
function of what is in the field as much as of how well it was seen.

Both tables are kept because they are what the search had to walk through, and
because each rejected form is one somebody will propose again.

What this module computes instead: the focus metric is per **cell**
----------------------------------------------------------------------

:func:`edge_focus` measures the width of the intensity transition at one
nucleus's own boundary, which is what defocus physically *is* — a property of
the optics, and therefore the same whatever the object's size. Over the same
fourfold radius range it spans **1.36x**. Its full derivation, and the sweep
over reducer, band width and operator that chose it, are in that function's own
docstring; the field's ``focus`` is the median of its cells' values
(:func:`field_focus`), so the two poolings are one measurement rather than two
statistics that can disagree.

All of that is synthetic, and it settles the *form* of the statistic rather
than its threshold. Whether the separation survives real optics, real stains
and real plates is the open question, and it is why nothing here decides what
counts as bad: this module measures, and the Review side scores.
"""

from __future__ import annotations

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
#: ``focus`` stays here because it is comparable across plates — it is a
#: transition width in pixels, so gain, pedestal, brightness, nucleus size and
#: crowding each move it by under 5 %, and only clipping moves it much (a
#: flat-topped nucleus reads sharper, up to 1.45x). The two readings are
#: complementary rather than redundant. The absolute cut (Review's
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


#: How far either side of the outline the transition profile is read, in
#: pixels. Wide enough to contain the rise at the defocus worth measuring,
#: narrow enough that a neighbour two cell-widths away does not enter it.
EDGE_REACH_PX = 8.0

#: The two levels whose separation IS the width, as fractions of the cell's
#: own contrast. The classic knife-edge 20-80 rise: far enough from both
#: plateaus that noise on them does not move the crossing, close enough that
#: the profile is still steep between them.
EDGE_RISE_LO = 0.20
EDGE_RISE_HI = 0.80

def edge_focus(image, mask) -> float | None:
    """How sharp one nucleus's own outline is. Higher is sharper.

    **It measures the WIDTH of the intensity transition at the boundary, not
    its energy**, and that choice is the whole design. Defocus is a property
    of the optics: it sets how far the intensity takes to travel from inside
    the object to outside. Brightness sets the *amplitude* of that transition,
    not its width. Every edge-energy statistic confounds the two — a dim cell
    and a blurred cell both have little gradient per pixel — while a distance
    in pixels is independent of gain, pedestal and amplitude by construction.

    The signed distance transform gives, for every pixel near the outline, how
    far it is from the boundary. Binning intensity by that distance gives the
    transition profile directly, and the rise distance from
    :data:`EDGE_RISE_LO` to :data:`EDGE_RISE_HI` of the cell's own contrast is
    the classic knife-edge width. The score is its reciprocal, so higher stays
    sharper.

    **Measured against the edge-energy form this replaced**, on a shot-noise
    fixture at a realistic cell size (20 px across):

    ===========================  ==========  ==========
    varying only                 energy      width
    ===========================  ==========  ==========
    brightness, 80x in amplitude 1.52x       **1.03x**
    nucleus size, 14-28 px       1.09x       **1.01x**
    read noise, 2 -> 30          1.02x       1.01x
    neighbours, 30 px -> contact 1.14x       **1.02x**
    chromatin texture, 0 -> 0.8  1.50x       **1.14x**
    saturation, clipped to 60 %  2.87x       **1.45x**
    gain and pedestal            exact       exact
    ===========================  ==========  ==========

    **And it is monotone.** The energy form turned back up past two stops of
    defocus and recovered **2.24x** from its minimum — a cut anywhere near the
    soft end excluded the mildly defocused cells and *kept* the catastrophic
    ones, which is the worst failure this subsystem could have. Over the same
    sweep out to eight stops this form falls monotonically, with **every cell
    measurable at every stop**. An SNR margin was tried first and rejected: it
    restored monotonicity by refusing the low-SNR cells, which at one stop of
    defocus left only 6 % of dim cells measurable against 100 % of bright ones
    — the brightness bias again, wearing a refusal.

    The remaining response to saturation is not all error: clipping genuinely
    does steepen a transition. Texture at 1.14x is the largest confounder left.

    ``None`` — never ``0.0`` — where the profile cannot be read: no contrast
    across the outline, too few pixels either side, or a profile that never
    crosses both levels. A cell that could not be measured and a cell measured
    and found soft are different facts (CLAUDE.md §7).
    """
    from scipy.ndimage import distance_transform_edt

    arr = np.asarray(image, dtype=np.float64)
    m = np.asarray(mask, dtype=bool)
    if arr.shape != m.shape or not m.any():
        return None
    finite = np.isfinite(arr)
    if not finite.all():
        arr = np.where(finite, arr, 0.0)

    # Signed distance to the outline: positive outside, negative inside.
    signed = distance_transform_edt(~m) - distance_transform_edt(m)
    near = np.abs(signed) <= EDGE_REACH_PX
    if near.sum() < _EDGE_MIN_PX * 4:
        return None

    # Median intensity per one-pixel distance bin. The median is what makes
    # this robust to shot noise and to a neighbour intruding on part of the
    # ring: a contaminated arc moves a mean and not a median.
    d, v = signed[near], arr[near]
    edges = np.arange(-EDGE_REACH_PX, EDGE_REACH_PX + 1.0, 1.0)
    which = np.digitize(d, edges) - 1
    prof_d, prof_v = [], []
    for b in range(len(edges) - 1):
        take = which == b
        if int(take.sum()) >= _EDGE_MIN_PX:
            prof_d.append(0.5 * (edges[b] + edges[b + 1]))
            prof_v.append(float(np.median(v[take])))
    if len(prof_d) < 5:
        return None
    prof_d = np.asarray(prof_d)
    prof_v = np.asarray(prof_v)

    inside = prof_v[prof_d < 0]
    outside = prof_v[prof_d > 0]
    if len(inside) < 2 or len(outside) < 2:
        return None
    # The plateaus, read from the two extremes of the profile rather than from
    # the whole of each side: the bins next to the boundary are the transition
    # and would drag both levels toward each other.
    i_hi = float(np.median(inside[:2]))
    i_lo = float(np.median(outside[-2:]))
    contrast = i_hi - i_lo
    if contrast <= 0.0:
        return None

    norm = (prof_v - i_lo) / contrast
    order = np.argsort(prof_d)
    dd, nn = prof_d[order], norm[order]

    def _cross(level) -> float | None:
        """Where the profile first crosses *level*, linearly interpolated.

        ``None`` when it never crosses -- a profile with no rise at that level
        is not a transition, and guessing a distance for it would be the
        undefined-as-zero substitution this module exists to avoid.
        """
        for k in range(len(nn) - 1):
            a, b = nn[k], nn[k + 1]
            if (a - level) * (b - level) <= 0 and a != b:
                t = (level - a) / (b - a)
                return dd[k] + t * (dd[k + 1] - dd[k])
        return None

    d_hi = _cross(EDGE_RISE_HI)
    d_lo = _cross(EDGE_RISE_LO)
    if d_hi is None or d_lo is None:
        return None
    width = float(d_lo - d_hi)
    if width <= 0.0:
        return None
    return float(1.0 / width)


#: Fewest pixels a one-pixel distance bin needs before its median is a
#: measurement rather than a sample. Also, times four, the fewest pixels the
#: whole neighbourhood of an outline needs to be worth profiling at all.
_EDGE_MIN_PX = 10


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
    pad = int(EDGE_REACH_PX) + 4  # the profile's reach, plus room to bin it
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
