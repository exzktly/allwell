"""Background-subtraction + illumination-correction math for the pipeline.

Extracted from ``process_microscopy.py`` so the correction algorithms live in
one cohesive, Qt-free module that both the pipeline and the GUI's validation
views can import. ``process_microscopy`` re-exports every public name here for
backward compatibility, so existing ``pm.subtract_background(...)`` call sites
(and the remote worker) are unchanged.

Module-level deps are intentionally minimal (numpy + one scipy morphology op);
the heavier scikit-image / scipy helpers are imported lazily inside the
functions that need them, exactly as in the original code.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import grey_opening

from core.flatfield import _FLATFIELD_SIGMA, apply_flatfield


def apply_tophat(image: np.ndarray, radius: int) -> np.ndarray:
    """Return white top-hat corrected image (background subtraction).

    Uses scipy.ndimage.grey_closing with a square (2*radius+1) structuring
    element.  A square footprint enables separable 1D decomposition inside
    scipy, giving O(radius) complexity per pixel instead of the O(radius²)
    of skimage's disk-based morphology.  For radius=50 on a 900×900 image
    this is ~100× faster than the skimage disk path with negligible
    difference in background-subtraction quality for typical fluorescence
    images where the background varies slowly.
    """
    img = image.astype(np.float32)
    size = 2 * radius + 1                      # side length of the square
    background = grey_opening(img, size=(size, size))   # opening = erosion→dilation
    return img - background                    # always ≥ 0 (opening is anti-extensive)


# Background-subtraction methods selectable via ``--background``.
BACKGROUND_METHODS = ("none", "top_hat", "sliding_disk", "fov_median", "empty_well",
                      "robust_mesh", "fft_bandpass")


def estimate_background_field(stack, sigma: "float | None" = None) -> "np.ndarray | None":
    """Additive background field from a stack of (empty-well) single-channel
    images: the per-pixel **median** (robust to stray signal that moves between
    fields), Gaussian-smoothed. Unlike :func:`estimate_basic_flatfield` this
    keeps the measured *level* (no mean-1 normalization) so it can be
    **subtracted**. Returns None when the stack is empty / shapes differ."""
    from scipy.ndimage import gaussian_filter
    if sigma is None:
        sigma = _FLATFIELD_SIGMA  # defined later in the flat-field section
    arrs = [np.asarray(a, dtype=np.float32) for a in stack]
    arrs = [a for a in arrs if a.ndim == 2]
    if not arrs:
        return None
    shp = arrs[0].shape
    arrs = [a for a in arrs if a.shape == shp]
    if not arrs:
        return None
    median = np.median(np.stack(arrs, axis=0), axis=0)
    return gaussian_filter(median, sigma=float(sigma)).astype(np.float32)


def _match_background_scale(img: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """Fit the measured background field to *img*'s actual background.

    The empty-well field ``B`` is captured at the reference well's exposure /
    illumination. If the target image differs in exposure, gain, illumination
    level, or carries a constant dark offset, subtracting the raw ``B`` leaves a
    residual that follows ``B``'s spatial shape — e.g. a corner-bright field
    leaves bright corners behind. So instead of subtracting ``B`` verbatim, fit
    the image's background as ``a·B + c`` (a least-squares regression of image
    intensity on the field over the background-dominated — dimmest ~60% —
    pixels, which excludes bright cells), and return that fitted field. The
    multiplicative term ``a`` absorbs the exposure/illumination mismatch and the
    constant ``c`` absorbs a uniform pedestal, so subtracting the result flattens
    the background regardless of where ``B`` is bright — with no assumption about
    its shape.

    ``a`` is clamped to ``[0.05, 20]`` so a near-constant / pathological field
    can't blow up the fit; on a degenerate field the original is returned.
    """
    i = np.asarray(img, dtype=np.float32)
    b = np.asarray(bg, dtype=np.float32)
    finite = np.isfinite(i) & np.isfinite(b)
    if int(finite.sum()) < 16:
        return b
    iv = i[finite]
    bv = b[finite]
    # Background-dominated subset: the dimmest ~60% of pixels. Bright cells sit
    # in the top tail and would otherwise pull the regression up.
    thr = float(np.percentile(iv, 60.0))
    mask = iv <= thr
    if int(mask.sum()) < 16:
        mask = np.ones_like(iv, dtype=bool)
    x = bv[mask]
    y = iv[mask]
    xm = float(x.mean())
    ym = float(y.mean())
    denom = float(((x - xm) ** 2).sum())
    if not np.isfinite(denom) or denom <= 0.0:
        return b
    a = float(((x - xm) * (y - ym)).sum() / denom)
    if not np.isfinite(a):
        return b
    a = float(min(20.0, max(0.05, a)))
    c = ym - a * xm
    fitted = (a * b + c).astype(np.float32)
    fitted[~np.isfinite(fitted)] = 0.0
    return fitted


def _floor_after_subtract(diff: np.ndarray) -> np.ndarray:
    """Force a background-subtracted image non-negative **and** finite.

    ``np.clip(x, 0, None)`` floors negatives but does **not** remove ``NaN`` /
    ``±Inf`` (``np.clip(nan, 0, None) is nan``). A measured empty-well field can
    carry a non-finite pixel (e.g. a NaN in the reference TIFFs survives the
    median + Gaussian smoothing), and ``img - bg`` then keeps it. Left in, those
    non-finite pixels crash StarDist's ClipperLib polygon NMS (an uncatchable
    C++ ``terminate``) and poison intensity sums. So zero out non-finite pixels
    first, then clip negatives to 0 (white-top-hat convention).
    """
    out = np.asarray(diff, dtype=np.float32)
    if not np.all(np.isfinite(out)):
        out = np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(out, 0.0, None)


def _isotropic_disk_tophat(img: np.ndarray, radius: int) -> np.ndarray:
    """Sliding-disk background: grayscale white top-hat with a flat *disk*
    footprint of the given radius, computed at full resolution.

    A disk of radius R slid under the intensity surface flattens regions wider
    than ~2·R (treated as background) and preserves narrower ones (kept as
    signal); the flatten/preserve threshold is set by the disk radius. Unlike
    ``top_hat``'s square footprint the disk is isotropic (no terraced square
    halos), and running at full resolution means there is no coarse grid to shift
    the threshold and stamp bright ~150 px disks into confluent regions (the
    failure of the reverted downscale approximation). The disk is built with
    skimage's ``decomposition="sequence"`` (a stack of small line erosions) so the
    opening is ~O(N) rather than O(N·radius²), keeping it fast at full resolution.
    """
    from skimage.morphology import disk, white_tophat
    r = max(1, int(round(float(radius))))
    try:
        footprint = disk(r, decomposition="sequence")
    except TypeError:                       # pragma: no cover - older skimage
        footprint = disk(r)
    # white_tophat = img - opening(img); opening is anti-extensive so it's ≥ 0.
    return np.clip(white_tophat(img, footprint).astype(np.float32), 0.0, None)


def _fft_butterworth_bandpass(img: np.ndarray, large_px: float,
                              small_px: float = 0.0, order: int = 2) -> np.ndarray:
    """Frequency-domain background flattening via a Butterworth band-pass.

    A Butterworth **high-pass** removes structures LARGER than ~``large_px`` (the
    slowly-varying background: uneven illumination, gradients, large blobs) —
    independent of object size, unlike morphological top-hat / rolling-ball,
    whose one structuring-element radius can never be both larger than every
    object *and* smaller than the background variation. ``small_px > 0`` adds a
    **low-pass** that also attenuates structures smaller than ~``small_px`` (pixel
    noise) for a true band-pass; ``small_px <= 0`` leaves a pure high-pass that
    preserves signal intensities (best for quantification).

    Computed in float64 complex internally (``np.fft`` never clips); only the
    final array is cast to float32, and no upper clamp is applied, so a response
    that exceeds the original max is preserved (no artificial ceiling). The
    returned image is already background-subtracted (≈ zero-mean); the caller
    floors it at 0.
    """
    a = np.asarray(img, dtype=np.float32)
    a = np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)
    h, w = a.shape
    # Reflect-pad before the FFT to suppress wrap-around / edge ringing.
    ph, pw = h // 2, w // 2
    padded = np.pad(a, ((ph, ph), (pw, pw)), mode="reflect")
    H, W = padded.shape
    fy = np.fft.fftfreq(H)[:, None]
    fx = np.fft.fftfreq(W)[None, :]
    D = np.sqrt(fy * fy + fx * fx)          # radial frequency, 0 at DC
    transfer = np.ones_like(D)
    n2 = 2 * int(order)
    # A feature of size s (px) sits near frequency f ~ 1/s.
    if large_px and large_px > 1:
        f_lo = 1.0 / float(large_px)        # high-pass cutoff (remove < f_lo)
        Dnz = np.where(D > 0, D, 1e-12)
        hp = 1.0 / (1.0 + (f_lo / Dnz) ** n2)
        hp[D == 0] = 0.0                     # kill DC (mean) explicitly
        transfer = transfer * hp
    if small_px and small_px > 1:
        f_hi = 1.0 / float(small_px)        # low-pass cutoff (remove > f_hi)
        lp = 1.0 / (1.0 + (D / f_hi) ** n2)
        transfer = transfer * lp
    out = np.fft.ifft2(np.fft.fft2(padded) * transfer).real
    return out[ph:ph + h, pw:pw + w].astype(np.float32)


def _robust_grid_background(img: np.ndarray, box: int,
                            sigma: float = 3.0) -> np.ndarray:
    """Robust local background on a coarse grid (SExtractor / photutils
    ``Background2D`` style), interpolated to full resolution.

    Each ``box``-sized tile yields a ``sigma``-clipped background level (the
    SExtractor mode ``2.5*median - 1.5*mean`` on uncrowded tiles, else the
    clipped median) — robust to bright / confluent objects, since most pixels in
    a tile are background. The mesh is median-filtered (drops tiles swamped by a
    single large object) then bicubically upsampled to a smooth full-resolution
    background. The background scale is set by the tile size, not by a
    structuring element that must out-size every object.

    ``sigma`` is the sigma-clip threshold (default 3.0): smaller rejects object /
    outlier pixels more aggressively (use on crowded fields), larger keeps more
    pixels (use when objects are sparse and you want a tighter estimate).
    """
    from scipy.ndimage import median_filter, zoom
    a = np.asarray(img, dtype=np.float32)
    h, w = a.shape
    sigma = float(sigma) if float(sigma) > 0 else 3.0
    box = max(8, int(round(float(box))))
    ny, nx = max(1, -(-h // box)), max(1, -(-w // box))   # ceil division
    Hh, Ww = ny * box, nx * box
    pad = a if (Hh == h and Ww == w) else np.pad(
        a, ((0, Hh - h), (0, Ww - w)), mode="reflect")
    tiles = (pad.reshape(ny, box, nx, box).transpose(0, 2, 1, 3)
                .reshape(ny, nx, box * box).astype(np.float32))
    tiles[~np.isfinite(tiles)] = np.nan
    data = tiles
    with np.errstate(invalid="ignore"):
        for _ in range(3):                                   # iterative sigma clip
            med = np.nanmedian(data, axis=2, keepdims=True)
            std = np.nanstd(data, axis=2, keepdims=True)
            data = np.where(np.abs(data - med) <= sigma * std, data, np.nan)
        med = np.nanmedian(data, axis=2)
        mean = np.nanmean(data, axis=2)
        std0 = np.nanstd(data, axis=2)
    # SExtractor mode where the clipped tile isn't too skewed (uncrowded), else
    # the plain clipped median.
    crowded = np.abs(mean - med) < 0.3 * np.where(std0 > 0, std0, np.inf)
    mesh = np.where(crowded, 2.5 * med - 1.5 * mean, med).astype(np.float32)
    if np.any(np.isfinite(mesh)):
        mesh[~np.isfinite(mesh)] = float(np.nanmedian(mesh[np.isfinite(mesh)]))
    else:
        mesh[:] = 0.0
    if ny >= 3 and nx >= 3:
        mesh = median_filter(mesh, size=3, mode="nearest")
    if mesh.shape == (1, 1):
        return np.full((h, w), float(mesh[0, 0]), dtype=np.float32)
    bg = zoom(mesh, (Hh / ny, Ww / nx), order=3, mode="nearest")
    return bg[:h, :w].astype(np.float32)


def subtract_background(image, radius: int, method: str = "sliding_disk",
                        bg_field: "np.ndarray | None" = None,
                        fft_small: float = 0.0,
                        mesh_ratio: float = 3.0,
                        mesh_sigma: float = 3.0) -> np.ndarray:
    """White background subtraction, dispatched by *method*.

    - ``top_hat`` — ``img - grey_opening(square)``: fast (separable 1-D passes)
      but the flat square footprint leaves anisotropic, *terraced* residuals,
      i.e. halos / hotspots next to bright objects. Current default.
    - ``sliding_disk`` — isotropic disk white-top-hat (``img - opening(disk)``),
      computed at FULL resolution via :func:`_isotropic_disk_tophat`. The smooth,
      isotropic baseline for uneven / structured background: a disk of the given
      radius flattens regions wider than ~2·radius and preserves narrower ones,
      isotropically (no square terracing like ``top_hat``) and fast (~1.8 s a
      1024² frame). An earlier downscale optimisation was reverted because a
      coarse grid shifts that flatten/preserve threshold and stamps bright ~150 px
      disks into confluent regions (which corrupt quantification).
    - ``fov_median`` — ``img - median(img)``: subtract a single scalar, the
      median intensity of the whole field of view. Because most pixels in a field
      are background, the median is a robust estimate of the flat background level
      (insensitive to bright cells/debris). Removes a uniform offset only — it
      does NOT flatten spatial gradients (use a morphological method or
      ``empty_well`` for that). No radius and no reference well needed.
    - ``empty_well`` — ``img - bg_field``: subtract a *measured* per-channel
      background (smoothed empty-well median, :func:`estimate_background_field`);
      no-op when no field is available for the channel.
    - ``robust_mesh`` — ``img - robust_grid_background``: a SExtractor /
      photutils ``Background2D``-style estimator (sigma-clipped local background
      on a coarse grid ~``radius/mesh_ratio`` per tile, mesh-median-filtered,
      bicubically interpolated). Robust to bright / confluent objects because the
      local background level is estimated per tile with a robust statistic, not
      by a structuring element that must out-size every object — handles uneven
      background the morphological methods can't flatten. ``mesh_ratio`` sets the
      tile size relative to ``radius`` (larger → coarser/smoother background);
      ``mesh_sigma`` is the per-tile sigma-clip threshold (smaller → rejects
      bright object pixels more aggressively on crowded fields).
    - ``fft_bandpass`` — a 2-D FFT Butterworth band-pass. A high-pass removes
      low-frequency background (illumination, gradients, large blobs) regardless
      of object size; ``fft_small > 0`` adds a low-pass that also denoises
      (true band-pass). ``radius`` is the large-structure (low-freq) cutoff;
      ``fft_small`` the small-structure (high-freq) cutoff in px (0 = high-pass
      only, which preserves signal intensities for quantification).

    Result is clipped at 0 (white-top-hat convention) so downstream sums stay
    non-negative.
    """
    img = np.asarray(image, dtype=np.float32)
    if method == "none":
        return img
    if method == "sliding_disk":
        return _isotropic_disk_tophat(img, int(radius))
    if method == "robust_mesh":
        # Tile ≈ radius/mesh_ratio so the grid has several nodes across a
        # background feature of size ~radius (the bicubic interpolation then
        # follows it), while each tile still holds far more background than
        # object pixels. Guard the ratio against 0 / negative input.
        ratio = float(mesh_ratio) if float(mesh_ratio) > 0 else 3.0
        box = max(8, int(round(int(radius) / ratio)))
        return _floor_after_subtract(
            img - _robust_grid_background(img, box, sigma=mesh_sigma))
    if method == "fft_bandpass":
        return _floor_after_subtract(
            _fft_butterworth_bandpass(img, large_px=int(radius), small_px=float(fft_small)))
    if method == "fov_median":
        finite = img[np.isfinite(img)]
        level = float(np.median(finite)) if finite.size else 0.0
        return _floor_after_subtract(img - level)
    if method == "empty_well":
        if bg_field is not None:
            bg = np.asarray(bg_field, dtype=np.float32)
            if bg.shape == img.shape:
                # Match the field to this image's background level first, so an
                # exposure/illumination mismatch can't leave a residual in the
                # field's shape (e.g. bright corners).
                bg = _match_background_scale(img, bg)
                return _floor_after_subtract(img - bg)
        return img
    return apply_tophat(img, int(radius))  # top_hat (default / back-compat)


def combined_empty_well_correct(image, bg_field, ff_field) -> np.ndarray:
    """Proper combined empty-well correction when the empty well drives *both*
    flat-field and background: subtract the measured additive background ``B``
    (``bg_field``, un-normalized), then divide by the mean-1 illumination field
    ``ff_field`` (= ``B/mean(B)``) — i.e. ``(raw − B) / (B/mean(B))``.

    This fixes the scale mismatch of doing them separately (flat-field divides,
    then a raw-scale field is subtracted from the rescaled image). It assumes the
    additive background **tracks the illumination** (true for media autofluor-
    escence under uneven excitation): then for ``raw = gain·signal + B`` with
    ``gain ∝ B`` it recovers ``signal``, and the empty well itself maps to ~0.
    Each field no-ops when absent / shape-mismatched.
    """
    img = np.asarray(image, dtype=np.float32)
    if bg_field is not None:
        b = np.asarray(bg_field, dtype=np.float32)
        if b.shape == img.shape:
            b = _match_background_scale(img, b)
            img = _floor_after_subtract(img - b)
    return apply_flatfield(img, ff_field)


