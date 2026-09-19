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

import json
import os
import tempfile
from pathlib import Path

import numpy as np
from scipy.ndimage import sobel

# The one "merge a named block into the sidecar" writer. Borrowed rather than
# re-implemented: a second atomic merge would be a second set of rules about
# what happens when the file is missing or malformed, and the two would
# diverge on the first bug fixed in only one of them. It lives in
# ``run_report`` because that block was the first to need it; that is a
# historical fact about the sidecar, not a layer this module sits above.
from pipeline.run_report import merge_into_pipeline_info

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


def group_record(*, acquisition: dict, labels, corrected_image, areas,
                 fov: str = "", timepoint: str = "") -> dict:
    """One FOV-group's quality record: the pre-correction half plus the rest.

    *acquisition* is what the loader measured **before** anything rewrote the
    array — focus, saturation and dynamic range, in that state for the reasons
    :func:`saturated_fraction` and the module docstring give. The three
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
    }
    record.update(acquisition)
    return record


# ── the record on disk ───────────────────────────────────────────────────────
#
# Same mechanism ``pipeline/run_report.py`` uses and for the same reasons: one
# small JSON per well, dropped as the well worker finishes, collected by the
# orchestrator into a named block inside ``pipeline_info.json``, then removed.
# Process-safe by construction — one file per well, written atomically, never
# appended to — so it works identically for the local worker pool, folder mode
# and remote runs.
#
# Deliberately **not** folded into ``run_report``. Coverage accounting and
# quality measurement answer different questions; ``run_report``'s payload is
# already merged across a remote fetch by a function with its own semantics; and
# a reader asking "was this well complete?" should not have to step over six
# float metrics per field to find out.

#: Hidden so it never looks like dataset output; removed once collected.
FIELD_QUALITY_DIRNAME = ".aw_field_quality"

#: The key the collected block takes inside ``pipeline_info.json``.
FIELD_QUALITY_BLOCK = "field_quality"


def _quality_dir(output_dir) -> Path:
    return Path(output_dir) / FIELD_QUALITY_DIRNAME


def write_well_field_quality(output_dir, well_label: str, *, fields: list[dict],
                             measured_of: int | None = None) -> Path | None:
    """Record one well's per-field metrics. Best-effort: never fails the run.

    *fields* is one mapping per FOV-group measured, each carrying whatever
    addresses it (``fov``, ``timepoint``) plus the metric values.

    ``measured_of`` is how many FOV-groups the well *has*, against
    ``len(fields)`` measured. **The gap is the whole of what a resumed or
    incremental run must not hide.** ``_process_or_resume`` returns cached
    records without calling ``process_image_group`` at all, and
    ``well_skip.incremental_for_well`` skips whole wells, so a block covering
    only what was re-processed is a biased subset — and scored as though it
    covered the plate, every field's robust z is wrong with nothing in the
    artifact saying so.
    """
    payload = {
        "well": str(well_label),
        "fields": list(fields),
        "measured": len(fields),
        "measured_of": (None if measured_of is None else int(measured_of)),
    }
    try:
        d = _quality_dir(output_dir)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{well_label}.json"
        fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".tmp_", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return path
    except OSError:
        # A run that cannot write its own bookkeeping must still produce data.
        return None


def well_is_partial(record: dict) -> bool:
    """Whether a well's record covers fewer fields than the well holds."""
    total = record.get("measured_of")
    if total is None:
        return False
    return int(record.get("measured", 0)) < int(total)


def collect_field_quality(output_dir) -> dict:
    """Merge the per-well files into one block.

    ``partial_wells`` is the list a consumer needs in order to say "this plate
    was only partly measured" without re-deriving it — and it is the difference
    between a scorer that knows its sample is biased and one that does not.
    """
    d = _quality_dir(output_dir)
    wells: dict[str, dict] = {}
    if d.is_dir():
        for path in sorted(d.glob("*.json")):
            try:
                rec = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            wells[str(rec.get("well") or path.stem)] = rec
    partial = sorted(w for w, r in wells.items() if well_is_partial(r))
    return {
        "wells": wells,
        "partial_wells": partial,
        "measured": sum(int(r.get("measured", 0)) for r in wells.values()),
        "measured_of": sum(int(r.get("measured_of") or 0)
                           for r in wells.values()) or None,
    }


def clear_field_quality(output_dir) -> None:
    """Drop the staging directory once its contents are in ``pipeline_info``."""
    d = _quality_dir(output_dir)
    try:
        for path in d.glob("*"):
            path.unlink(missing_ok=True)
        d.rmdir()
    except OSError:
        pass


def summarize(block: dict | None) -> str:
    """One human line about a collected block, or ``""`` when there is none.

    The single composition of "what did field quality find", so the end-of-run
    log and the Analyze tab's pre-flight strip cannot say different things
    about the same folder — the pre-flight **quotes** this rather than
    deriving its own sentence from the same numbers (CLAUDE.md §0.3: report,
    do not decide; and a second derivation is a second chance to describe
    something that did not happen).

    Empty for an absent block, because a plate nobody measured and a plate
    measured and found clean must not read alike and a blank line is how the
    caller says nothing rather than the wrong thing.

    Qt-free and ``review``-free: it counts what the block records and does not
    score anything. Scoring is :mod:`review.domain.field_quality_scores`'
    job, needs the whole plate, and is the Review side's to run.
    """
    if not isinstance(block, dict) or not block.get("wells"):
        return ""
    wells = block["wells"]
    partial = list(block.get("partial_wells") or ())
    measured = int(block.get("measured", 0))
    total = block.get("measured_of")
    if partial:
        shown = ", ".join(partial[:8]) + ("…" if len(partial) > 8 else "")
        return (
            f"FIELD QUALITY PARTIAL: {measured:,} of "
            f"{int(total or 0):,} field(s) measured across {len(wells):,} "
            f"well(s); {len(partial)} well(s) only partly measured ({shown}). "
            f"A plate-wide comparison over this block would be drawn from a "
            f"biased sample, so Review will decline to score it — re-run "
            f"those wells with --force to complete it."
        )
    return (f"Field quality: {measured:,} field(s) measured across "
            f"{len(wells):,} well(s).")


def finalize_field_quality(output_dir, info_path, *, atomic_writer, log) -> None:
    """Fold the per-well quality files into ``pipeline_info.json``.

    Called once at the end of a run, mirroring
    :func:`pipeline.run_report.finalize_run_report` — same staging directory
    shape, same atomic merge, same best-effort posture. A run must not fail
    because it could not describe itself, and a plate with no block at all is
    correctly read by the Review side as "analysed before field quality was
    recorded" rather than as a plate measured and found clean.

    The partial count is logged rather than left for a reader to derive,
    because it is the one fact that decides whether the block can be scored at
    all: a resumed or incremental run measures only the fields it
    re-processed, so a plate-wide comparison drawn from it is biased, and the
    scorer refuses rather than reporting a number.
    """
    try:
        block = collect_field_quality(output_dir)
        if block.get("wells"):
            merge_into_pipeline_info(
                info_path, {FIELD_QUALITY_BLOCK: block},
                atomic_writer=atomic_writer)
            message = summarize(block)
            if block.get("partial_wells"):
                log.warning("%s", message)
            else:
                log.info("%s", message)
        clear_field_quality(output_dir)
    except Exception as exc:
        log.warning("Could not record field quality: %s", exc)


# ── Remote fetch: the sidecar is overwritten, the block must not be ─────────

def read_field_quality_block(info_path) -> dict | None:
    """The ``field_quality`` block currently in *info_path*, or ``None``."""
    path = Path(info_path)
    if not path.exists():
        return None
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    block = info.get(FIELD_QUALITY_BLOCK) if isinstance(info, dict) else None
    return block if isinstance(block, dict) else None


def merge_fetched_field_quality(info_path, prior_block: dict | None) -> bool:
    """Fold *prior_block* back into the sidecar a remote fetch just overwrote.

    The same defect :func:`pipeline.run_report.merge_fetched_run_report`
    exists for, one block over, and worse here. A remote output directory
    starts empty, so the sidecar a remote run writes covers only the wells
    that run processed — on an incremental run, only the ones the upload split
    sent. Pulling that file over the local one replaces a whole plate's
    measurements with a handful.

    For coverage records that loses history. For field quality it produces a
    **wrong number**: the acquisition metrics are scored as robust z against
    the plate, so a block silently reduced to the eight wells a remote
    incremental run touched gives every one of those wells a z computed
    against its own eight-well median. Nothing in the artifact would say so,
    and the shortfall accounting cannot catch it either — each of those wells
    is individually complete, so ``partial_wells`` stays empty and the scorer
    proceeds.

    Fetched records win per well (they are this run's, and newer); prior
    records survive for every well the fetched block does not mention. The
    derived fields are recomputed from the merged map rather than merged
    themselves, so they cannot disagree with the wells they summarise.
    Returns True iff the file was rewritten.
    """
    if not prior_block:
        return False
    path = Path(info_path)
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(info, dict):
        return False
    fetched = info.get(FIELD_QUALITY_BLOCK)
    fetched_wells = fetched.get("wells") if isinstance(fetched, dict) else None
    prior_wells = prior_block.get("wells")
    if not isinstance(prior_wells, dict):
        return False
    merged = dict(prior_wells)
    if isinstance(fetched_wells, dict):
        merged.update(fetched_wells)
    if merged == fetched_wells:
        return False
    info[FIELD_QUALITY_BLOCK] = {
        "wells": merged,
        "partial_wells": sorted(w for w, r in merged.items()
                                if well_is_partial(r)),
        "measured": sum(int((r or {}).get("measured", 0))
                        for r in merged.values()),
        "measured_of": sum(int((r or {}).get("measured_of") or 0)
                           for r in merged.values()) or None,
    }
    try:
        path.write_text(json.dumps(info, indent=2))
    except OSError:
        return False
    return True


def records_for_scoring(block: dict | None) -> list[dict]:
    """Flatten a collected block into the rows the Review-side scorer takes.

    Each row carries its well and timepoint, because those are the axes the
    scorer groups on, and a ``partial_plate`` flag drawn from
    ``partial_wells`` — so a scorer handed a biased subset can say so rather
    than reporting a plate median it has no right to.
    """
    if not isinstance(block, dict):
        return []
    partial = set(block.get("partial_wells") or ())
    rows: list[dict] = []
    for well, rec in (block.get("wells") or {}).items():
        for fld in rec.get("fields") or ():
            row = dict(fld)
            row.setdefault("well", well)
            row["key"] = (well, fld.get("fov"), fld.get("timepoint"))
            row["partial_plate"] = well in partial
            rows.append(row)
    return rows
