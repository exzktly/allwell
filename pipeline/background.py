"""Background-subtraction + illumination-correction math for the pipeline.

Extracted from ``process_microscopy.py`` so the correction algorithms live in
one cohesive, Qt-free module that both the pipeline and the GUI's validation
views can import. Callers import from here; ``process_microscopy`` imports the
two it calls.

Module-level deps are intentionally minimal (numpy + one scipy morphology op);
the heavier scikit-image / scipy helpers are imported lazily inside the
functions that need them, exactly as in the original code.
"""

from __future__ import annotations

import logging

import numpy as np
from scipy.ndimage import grey_opening

from pipeline.flatfield import _FLATFIELD_SIGMA, apply_requested_flatfield

# The pipeline's logger, as in ``pipeline.stardist`` — a correction that could
# not be computed has to reach the run log, because the array it returns is
# indistinguishable from a corrected one.
log = logging.getLogger("process_microscopy")


def apply_tophat(image: np.ndarray, radius: int) -> np.ndarray:
    """Return white top-hat corrected image (background subtraction).

    Uses ``scipy.ndimage.grey_opening`` with a square (2*radius+1) structuring
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

#: Which settings each background method actually consults, read straight off
#: :func:`subtract_background`'s dispatch below.
#:
#: A run's receipt has to name the settings that shaped its numbers, and only
#: those. Listing a mesh ratio beside ``sliding_disk`` — which never sees one —
#: states that a number had an effect it did not have, and that is the same
#: class of untruth as omitting the ones that did. Kept here rather than in the
#: reader so the mapping cannot drift from the dispatch it describes;
#: ``tests/test_correction_parameter_roster.py`` fails if a method is added
#: without an entry, or if an entry names a parameter the dispatch does not take.
#:
#: ``background_well`` is not an argument of ``subtract_background`` — it is
#: what produced the ``bg_field`` handed to it — but it belongs here because it
#: is the setting a reader has to know to reproduce the run.
#: ``background_well_map`` is the same kind of setting and matters more: it says
#: *which* blank produced the field for a given well, so two wells of one plate
#: can be corrected against different pixels. A receipt naming the blanks and
#: not the pairing describes a run that could have been either one.
BACKGROUND_METHOD_PARAMS: dict[str, tuple[str, ...]] = {
    "none": (),
    "top_hat": ("radius",),
    "sliding_disk": ("radius",),
    "fov_median": (),
    "empty_well": ("background_well", "background_well_map"),
    "robust_mesh": ("radius", "mesh_ratio", "mesh_sigma"),
    "fft_bandpass": ("radius", "fft_small"),
}


def background_params_used(method: str) -> tuple[str, ...]:
    """The settings *method* consults; empty for an unknown or parameterless one."""
    return BACKGROUND_METHOD_PARAMS.get(str(method or "").strip().lower(), ())


def estimate_background_field(stack, sigma: float | None = None) -> np.ndarray | None:
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
    intensity on the field over the background pixels, found by growing a
    low-intensity seed and rejecting bright cells), and return that fitted field. The
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
    # A background-only subset to start from: the fifth of the pixels that sit
    # lowest above the blank. Bright cells sit in the top tail and would
    # otherwise pull the regression up, and a fifth is still background at 80%
    # cover, where the dimmest 60% this started from is mostly cells.
    above_blank = iv - bv
    mask = above_blank <= float(np.percentile(above_blank, _BG_SEED_PERCENT))
    if int(mask.sum()) < 16:
        mask = np.ones_like(iv, dtype=bool)
    fit = _fit_line(bv[mask], iv[mask])
    if fit is None:
        return b
    # Then grow it to the whole background. The seed is the low tail of the
    # background's noise, so a line through it alone sits low; each round keeps
    # every pixel within ``_BG_CLIP_SIGMA`` robust standard deviations of the
    # fitted background — measured on the residual's lower half, which cells
    # cannot reach — and refits, until the set stops changing. Cells are bright
    # outliers of that residual and never join it. (Fitting the dimmest 60%
    # once, as this did, put cell pixels in the fit past 40% cover: identical
    # cells of mean 500 read 418 at 52%.)
    for _round in range(_BG_CLIP_ROUNDS):
        resid = iv - (fit[0] * bv + fit[1])
        centre = float(np.median(resid[mask]))
        below = resid[mask][resid[mask] <= centre]
        sigma = 1.4826 * float(np.median(centre - below)) if below.size else 0.0
        if not np.isfinite(sigma) or sigma <= 0.0:
            break
        keep = np.abs(resid - centre) <= _BG_CLIP_SIGMA * sigma
        if int(keep.sum()) < 16 or bool(np.array_equal(keep, mask)):
            break
        refit = _fit_line(bv[keep], iv[keep])
        if refit is None:
            break
        mask, fit = keep, refit
    a, c = fit
    fitted = (a * b + c).astype(np.float32)
    fitted[~np.isfinite(fitted)] = 0.0
    return fitted


#: Rounds of outlier rejection in :func:`_match_background_scale`, and the width
#: of what counts as background in each, in robust standard deviations.
_BG_CLIP_ROUNDS = 20
_BG_CLIP_SIGMA = 3.0
#: The share of the frame :func:`_match_background_scale` takes as certainly
#: background to start its fit from. The fit therefore needs at least this much
#: of the frame to be background; a field more confluent than that has no
#: background left to fit a blank to.
_BG_SEED_PERCENT = 20.0


def _fit_line(x: np.ndarray, y: np.ndarray) -> tuple[float, float] | None:
    """Least-squares ``y = a·x + c`` with ``a`` clamped to ``[0.05, 20]``; ``None`` on a degenerate fit.

    A field with no spatial variation (a flat blank) is degenerate: it has no
    shape for a scale to be fitted against, so the caller subtracts it as
    measured. "No variation" is judged against the field's own level, not
    against zero — a flat ``float32`` field's mean rounds, the sum of squares
    comes out a hair above zero, and a slope fitted to that is noise.
    """
    xm = float(x.mean())
    ym = float(y.mean())
    denom = float(((x - xm) ** 2).sum())
    if not np.isfinite(denom) or denom <= 1e-6 * max(1.0, xm * xm) * x.size:
        return None
    a = float(((x - xm) * (y - ym)).sum() / denom)
    if not np.isfinite(a):
        return None
    a = float(min(20.0, max(0.05, a)))
    return a, ym - a * xm


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
    if not (large_px and large_px > 1) and not (small_px and small_px > 1):
        # Neither cutoff is usable, so ``transfer`` is still all ones and the
        # round trip returns the input: a background subtraction that subtracts
        # nothing, and whose output no reader can tell from a corrected frame.
        # A cutoff of 1 px or less asks to remove structures larger than one
        # pixel, i.e. everything, which is why the guards reject it — the
        # measured behaviour is a cliff between radius 1 (no-op) and radius 2.
        log.warning(
            "Background: fft_bandpass has no usable cutoff (radius=%r, "
            "fft_small=%r; both must exceed 1 px) — the frame is returned "
            "UNCHANGED and is NOT background-subtracted.", large_px, small_px,
        )
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
    from scipy.ndimage import median_filter
    a = np.asarray(img, dtype=np.float32)
    h, w = a.shape
    sigma = float(sigma) if float(sigma) > 0 else 3.0
    box = max(8, int(round(float(box))))
    rows, centre_y = _even_tiles(h, box)
    cols, centre_x = _even_tiles(w, box)
    ny, nx = rows.shape[0], cols.shape[0]
    tiles = (a[rows[:, None, :, None], cols[None, :, None, :]]
             .reshape(ny, nx, -1).astype(np.float32))
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
        # Not one tile yielded a finite level — a frame that is entirely NaN or
        # Inf. Zero is the only mesh left to return, and subtracting it is the
        # identity: the caller gets back a frame that looks corrected. Say so.
        log.warning(
            "Background: robust_mesh could not estimate a single one of the "
            "%d×%d tiles (no finite pixels) — the mesh is zero and the frame is "
            "NOT background-subtracted.", ny, nx,
        )
        mesh[:] = 0.0
    if ny >= 3 and nx >= 3:
        # The 3x3 median that drops a tile swamped by one large object. Run on
        # the mesh continued one node past each edge at its own slope, then
        # cropped back: with the edge row merely repeated, a border node's
        # neighbourhood is lopsided, and under a gradient that runs along the
        # border as well as across it the median is no longer the node itself
        # (0.3 and 0.1 counts per pixel on 100 px tiles moved every border node
        # by 10, which the edge then carried as a 16-count error).
        ext, _ = _extend_nodes(mesh.astype(np.float64), centre_y, 0)
        ext, _ = _extend_nodes(ext, centre_x, 1)
        mesh = median_filter(ext, size=3, mode="nearest")[1:-1, 1:-1].astype(np.float32)
    if mesh.shape == (1, 1):
        return np.full((h, w), float(mesh[0, 0]), dtype=np.float32)
    return _upsample_tile_mesh(mesh, centre_y, centre_x, h, w)


def _even_tiles(length: int, box: int) -> tuple[np.ndarray, np.ndarray]:
    """One axis cut into equal tiles of real pixels: ``(pixel index per tile, tile centres)``.

    ``ceil(length / box)`` tiles, each ``ceil(length / n)`` pixels — *box* when
    it divides the axis, the nearest size below it otherwise — spread evenly
    from the first pixel to the last, overlapping by a pixel here and there
    where the division is not exact. Every tile is whole and holds only pixels
    of the frame. Padding the frame out to a multiple of *box* by reflection,
    which this replaces, made the last tile a mix of real and mirrored pixels:
    its level then belongs to no one position, and on a ramp the background was
    wrong by up to 0.3 of a tile's gradient at that edge.
    """
    n = max(1, -(-length // box))                     # ceil division
    size = -(-length // n)
    starts = np.rint(np.linspace(0, length - size, n)).astype(np.intp)
    return starts[:, None] + np.arange(size, dtype=np.intp), starts + (size - 1) / 2.0


def _extend_nodes(mesh: np.ndarray, at: np.ndarray, axis: int) -> tuple[np.ndarray, np.ndarray]:
    """*mesh* and its node positions *at* with one node added at each end of *axis*.

    The added node sits one node spacing further out and continues the mesh's
    slope there. A single node has no slope to continue, so it is repeated a
    pixel either side.
    """
    m = np.moveaxis(mesh, axis, 0)
    if m.shape[0] == 1:
        first, last, before, after = m[0], m[0], at[0] - 1.0, at[0] + 1.0
    else:
        first, last = 2.0 * m[0] - m[1], 2.0 * m[-1] - m[-2]
        before, after = 2.0 * at[0] - at[1], 2.0 * at[-1] - at[-2]
    return (np.moveaxis(np.concatenate([first[None], m, last[None]], axis=0), 0, axis),
            np.concatenate([[before], at, [after]]))


def _upsample_tile_mesh(mesh: np.ndarray, centre_y: np.ndarray, centre_x: np.ndarray,
                        h: int, w: int) -> np.ndarray:
    """A tile mesh as a full-resolution ``(h, w)`` background.

    A tile's level is the background at the tile's **centre**, and that is
    where its node is placed. Stretching the mesh so that its first and last
    nodes land on the frame's first and last pixels — what a plain image zoom
    does — moves every node toward the edges by up to half a tile, and the
    background is then wrong by that distance times the local gradient across
    the *whole* frame, not only at its rim: a ramp of 0.5 counts per pixel on
    100 px tiles read +24.8 at the left edge, +11.1 a third of the way in and
    -26.8 at the right.

    Between the centres the surface is a cubic spline. Outside the outermost
    centres, the half tile at each edge, the mesh is continued at its own slope
    (:func:`_extend_nodes`) rather than held at the last node's level, which
    would leave half a tile's worth of gradient unsubtracted there.
    """
    from scipy.interpolate import RectBivariateSpline

    ext, node_y = _extend_nodes(mesh.astype(np.float64), centre_y, 0)
    ext, node_x = _extend_nodes(ext, centre_x, 1)
    # ``kx`` is the degree along the FIRST axis given, which here is y.
    spline = RectBivariateSpline(node_y, node_x, ext,
                                 kx=min(3, node_y.size - 1), ky=min(3, node_x.size - 1))
    return spline(np.arange(h), np.arange(w)).astype(np.float32)


def subtract_background(image, radius: int, method: str = "sliding_disk",
                        bg_field: np.ndarray | None = None,
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
        filtered = _fft_butterworth_bandpass(img, large_px=int(radius), small_px=float(fft_small))
        # The filter removes the frame's mean, so its output is zero-mean, not
        # zero-background: the more of the field the cells cover, the further
        # below zero the background sits, and flooring it there took that much
        # off every cell (identical cells of mean 500 read 493 with 20 cells in
        # the field and 337 with 3,000, at radius 200). Re-reference to the
        # filtered frame's median, the same background level ``fov_median``
        # uses, so the background is what sits at zero.
        # Only when the high-pass ran: with no usable cutoff the frame comes
        # back unchanged (and says so), and has no mean removed to put back.
        finite = filtered[np.isfinite(filtered)]
        level = float(np.median(finite)) if finite.size and int(radius) > 1 else 0.0
        return _floor_after_subtract(filtered - level)
    if method == "fov_median":
        finite = img[np.isfinite(img)]
        if not finite.size:
            # Every pixel is NaN or Inf, so there is no median to subtract. The
            # old code substituted 0.0 — a measured level of zero and "I could
            # not measure a level" written the same way, and the second one is
            # not a measurement at all.
            log.warning(
                "Background: fov_median found no finite pixel in this frame, so "
                "no background level could be measured — the frame is NOT "
                "background-subtracted (only its non-finite pixels are zeroed).",
            )
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
            log.warning(
                "Background: the empty-well field is %s and this frame is %s, "
                "so it cannot be subtracted — the frame is returned UNCHANGED "
                "and is NOT background-subtracted. A blank acquired at a "
                "different binning or crop from the plate is the usual cause.",
                bg.shape, img.shape,
            )
        else:
            log.warning(
                "Background: empty_well was requested but no background field "
                "was built for this channel — the frame is returned UNCHANGED "
                "and is NOT background-subtracted.",
            )
        # The fourth identity return in this function, and the one that used to
        # be silent. ``_require_empty_well_models`` covers only the case where
        # *no* model was built at all, so a per-channel gap — and a shape
        # mismatch in particular — passed straight through, written under the
        # usual ``_tophat`` filename with nothing downstream able to tell it
        # from a corrected frame.
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
    A field that is absent or of another shape is skipped, and each skip is
    warned about: the frame is otherwise written under the usual corrected
    filename with nothing to tell it from a corrected one.
    """
    img = np.asarray(image, dtype=np.float32)
    if bg_field is None:
        log.warning(
            "Background: empty_well was requested but no background field "
            "was built for this channel — the frame is NOT background-subtracted.",
        )
    else:
        b = np.asarray(bg_field, dtype=np.float32)
        if b.shape == img.shape:
            b = _match_background_scale(img, b)
            img = _floor_after_subtract(img - b)
        else:
            log.warning(
                "Background: the empty-well field is %s and this frame is %s, "
                "so it cannot be subtracted — the frame is NOT "
                "background-subtracted. A blank acquired at a different binning "
                "or crop from the plate is the usual cause.",
                b.shape, img.shape,
            )
    return apply_requested_flatfield(img, ff_field)


