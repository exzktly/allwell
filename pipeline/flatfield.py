"""Qt-free flat-field (illumination) estimators shared by the pipeline and GUI.

The pipeline (``process_microscopy.py``) applies these at run time; the Review
viewer recomputes them on demand for the Segmentation tab's "Flat-field only"
validation view. Lives in the shared ``core`` package — like
``pipeline.auto_threshold`` and ``pipeline.shape_features`` — so the pipeline can import
it without pulling in ``review`` / Qt, and the viewer can import it without
pulling in the heavy pipeline ML stack. Dependencies: numpy + scipy only.

A "flat-field" here is a **mean-1 multiplicative illumination field**: dividing
an image by it flattens slow illumination gradients (vignetting / uneven
excitation) while preserving cell-scale structure, and the mean-1 normalization
keeps intensity magnitudes comparable so downstream gating thresholds still
make sense.
"""

from __future__ import annotations

import numpy as np

# Gaussian smoothing scale (px) for illumination estimation, and the min value
# of a mean-1 field (caps img/ff amplification at 1/FLOOR = 10x so a near-zero
# empty-well region can't explode the division).
FLATFIELD_SIGMA = 50.0
FLATFIELD_FLOOR = 0.1

# Back-compat aliases for the original ``process_microscopy`` names.
_FLATFIELD_SIGMA = FLATFIELD_SIGMA
_FLATFIELD_FLOOR = FLATFIELD_FLOOR

#: Which settings each flat-field mode consults — the counterpart of
#: :data:`pipeline.background.BACKGROUND_METHOD_PARAMS`, and read the same way
#: by a run's receipt.
#:
#: Three of the four take nothing a user chose: ``gaussian`` estimates its field
#: from the frame in front of it, and ``basic`` estimates one from the plate's
#: own images. Only ``empty_well`` is told which wells to measure, and that is
#: the setting somebody has to know to reproduce the run — together with
#: ``background_well_map``, which says which of those wells measured which
#: block of the plate.
#:
#: The mode roster itself is ``pipeline.cli_args.FLATFIELD_MODES``;
#: ``tests/test_correction_parameter_roster.py`` holds the two in step.
FLATFIELD_MODE_PARAMS: dict[str, tuple[str, ...]] = {
    "none": (),
    "gaussian": (),
    "basic": (),
    "empty_well": ("background_well", "background_well_map"),
}


def flatfield_params_used(mode: str) -> tuple[str, ...]:
    """The settings *mode* consults; empty for an unknown or parameterless one."""
    return FLATFIELD_MODE_PARAMS.get(str(mode or "").strip().lower(), ())


def normalize_flatfield(field: np.ndarray) -> np.ndarray | None:
    """Normalize an illumination field to mean 1.0 so dividing by it preserves
    overall image scale. Returns None if the field is degenerate."""
    field = np.asarray(field, dtype=np.float32)
    m = float(np.mean(field)) if field.size else 0.0
    if not np.isfinite(m) or m <= 0.0:
        return None
    out = field / m
    out[~np.isfinite(out)] = 1.0
    # Bound the amplification of img/ff in apply_flatfield. A dim empty-well
    # region drives ff toward 0; without a floor, img/ff explodes (e.g. ff=0.005
    # → 200x) and feeds StarDist out-of-distribution values that abort ClipperLib.
    # Flooring the mean-1 field at FLATFIELD_FLOOR caps amplification at
    # 1/FLATFIELD_FLOOR (=10x) and subsumes the old `out <= 0` guard. Real
    # vignetting is < ~3x (field stays > ~0.33), so legitimate correction is
    # untouched; only pathological empty-well division is clipped.
    np.maximum(out, FLATFIELD_FLOOR, out=out)
    return out.astype(np.float32)


def estimate_gaussian_flatfield(image: np.ndarray, sigma: float = FLATFIELD_SIGMA) -> np.ndarray | None:
    """Estimate a smooth illumination field by heavily Gaussian-blurring *image*.

    Dividing the image by the returned mean-1 field flattens slow illumination
    gradients (vignetting / uneven excitation) while preserving cell-scale
    structure. Self-referential: computed per image, no reference well needed.
    """
    from scipy.ndimage import gaussian_filter
    blurred = gaussian_filter(np.asarray(image, dtype=np.float32), sigma=float(sigma))
    return normalize_flatfield(blurred)


def estimate_basic_flatfield(stack, sigma: float = FLATFIELD_SIGMA) -> np.ndarray | None:
    """Pure-numpy BaSiC-style flat-field from a stack of single-channel images.

    Estimates the multiplicative illumination profile shared across the stack:
    the per-pixel median (robust to cells/debris that move between fields),
    smoothed and normalized to mean 1. This approximates BaSiC's flat-field
    without its L1/low-rank optimisation — and without ``jax``/``basicpy``.
    Returns None when the stack is empty or shapes are inconsistent.
    """
    from scipy.ndimage import gaussian_filter
    arrs = [np.asarray(a, dtype=np.float32) for a in stack]
    arrs = [a for a in arrs if a.ndim == 2]
    if not arrs:
        return None
    shp = arrs[0].shape
    arrs = [a for a in arrs if a.shape == shp]
    if not arrs:
        return None
    median = np.median(np.stack(arrs, axis=0), axis=0)
    return normalize_flatfield(gaussian_filter(median, sigma=float(sigma)))


def apply_flatfield(image: np.ndarray, flatfield: np.ndarray | None) -> np.ndarray:
    """Divide *image* by a mean-1 *flatfield* (no-op when None or shape-mismatched).

    Returns float32; the mean-1 normalization keeps intensity magnitudes
    comparable to the uncorrected image so downstream gating thresholds still
    make sense.
    """
    if flatfield is None:
        return image
    img = np.asarray(image, dtype=np.float32)
    ff = np.asarray(flatfield, dtype=np.float32)
    if ff.shape != img.shape:
        return image
    with np.errstate(divide="ignore", invalid="ignore"):
        out = img / ff
    out[~np.isfinite(out)] = 0.0
    return out


# Back-compat alias (``process_microscopy`` historically called the private name).
_normalize_flatfield = normalize_flatfield
