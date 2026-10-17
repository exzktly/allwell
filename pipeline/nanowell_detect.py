"""Finding the nanowell lattice and its sites in a phase-channel image.

Qt-free; numpy and scipy only. Nanowells are **light disks on a black lattice**;
a bubble turns a nanowell black, so an occluded site is a black disk on the black
lattice. The order of work (tracker §3.3) is lattice first, sites second:

1. :func:`estimate_lattice_fft` reads the lattice's pitch and angle from the peaks
   of the image's Fourier transform. That uses the whole array at once, so it
   survives a bubble over a tenth of it: those sites contribute nothing and the
   rest still do.
2. :func:`detect_sites` finds candidate sites with a matched filter for a light
   disk on a dark ground at the plate format's radius, and refines the lattice
   with ``pipeline.nanowell_lattice.fit_lattice`` on the candidates.
3. :func:`score_fiducial` scores each central site's squareness and measures the
   square's orientation, and :func:`check_orientation` compares that with the
   lattice axis the fit chose (tracker N3a).
4. :func:`refine_sites` fits every roster site's centre and radius from its radial
   edge profile; a site where the fit fails keeps its lattice position and is
   marked ``predicted``. **No site is dropped for lack of evidence.**

Units: image functions take pixels and the pixel size; results in micrometres say
so in their names. Image axes are x right, y down, as everywhere in the geometry
modules. **Positions in micrometres are in the frame where pixel ``i`` covers
``[i, i + 1) * pixel_size``**, the same frame the tile placements and the rendered
tiles use, so the centre of pixel ``i`` is ``(i + 0.5) * pixel_size`` and an array
index is ``position / pixel_size - 0.5``. Mixing the two is a half-pixel shift.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from pipeline.nanowell_lattice import FiducialCandidate, LatticeFit, fit_array_boundary, fit_lattice

FITTED = "fitted"
PREDICTED = "predicted"

#: Peaks of the spectrum count as one lattice's first order when their magnitudes are within
#: this fraction of the strongest's.
_PEAK_MAGNITUDE_TOLERANCE = 0.08
#: ... and their directions within this many degrees of 60 or 90 from it.
_PEAK_ANGLE_TOLERANCE_DEG = 8.0
#: A peak must carry at least this share of the strongest peak's power to be taken as a lattice's
#: first order when it lies below it in frequency.
_FUNDAMENTAL_POWER_FRACTION = 0.02
#: The orientation tolerance for the square against the lattice axis (tracker N3a).
SQUARE_AXIS_TOLERANCE_DEG = 15.0
#: How far the fitted basis may depart from an ideal lattice (length ratio, 5 degrees) before the
#: lattice is called stretched rather than used.
_MAX_BASIS_DISTORTION = 0.05
#: Below this a nanowell is too few pixels across for a ray fit to be trusted. Measured on rendered arrays with
#: noise: 8 % of 1,285 sites fitted at a 5.8 px radius, 24 % at 7.1 px, 57 % at 8.3 px, and all but one of 313
#: at 11.6 px. At a 10x objective's 0.65 um per pixel a 50 um nanowell is 38 px in radius, so this bites only
#: on a heavily downsampled montage, which is not what refinement reads.
MIN_REFINE_RADIUS_PX = 10.0
#: The width, in pixels, of an in-focus rim in this imaging (plateau drop over steepest slope); softness beyond
#: it relaxes the scatter limit.
_SHARP_EDGE_WIDTH_PX = 4.0
#: Rays cast per site when fitting its edge, and the share that must find an edge.
_RAYS = 32
_MIN_RAY_FRACTION = 0.6


# ── N1: the lattice from the Fourier peaks ───────────────────────────────────
@dataclass(frozen=True)
class LatticeEstimate:
    ok: bool
    reason: str
    kind: str | None = None             # "hex" or "square"
    pitch_um: float | None = None
    #: Direction of the shortest lattice vector, folded into one period of the lattice
    #: (-30..30 degrees hex, -45..45 square), in the geometry modules' convention.
    angle_deg: float | None = None
    confidence: float | None = None     # strongest peak's power over the ring's median power
    peaks: int = 0                      # first-order peaks that agreed


def _zoom_peak(image, fy: float, fx: float, step: float, span: int = 10) -> tuple[float, float, float]:
    """The frequency near (fy, fx) with the largest spectrum power, found with a local DFT.

    The FFT samples frequencies a bin apart, which at a pitch of 40 px on a 4096 px image is
    a one-in-a-hundred step in pitch; evaluating the transform on a grid a tenth of a bin
    wide around each peak refines it to a thousandth without padding the whole image.
    """
    h, w = image.shape
    ys, xs = np.arange(h), np.arange(w)
    fys = fy + np.arange(-span, span + 1) * step / h
    fxs = fx + np.arange(-span, span + 1) * step / w
    left = np.exp(-2j * np.pi * np.outer(fys, ys))          # (K, h)
    right = np.exp(-2j * np.pi * np.outer(xs, fxs))         # (w, K)
    power = np.abs(left @ image @ right) ** 2
    k = np.unravel_index(int(np.argmax(power)), power.shape)
    return float(fys[k[0]]), float(fxs[k[1]]), float(power[k])


def estimate_lattice_fft(image, pixel_size_um: float, *, expected_pitch_um: float | None = None,
                         kind: str = "auto") -> LatticeEstimate:
    """Pitch, angle and kind of the lattice from the peaks of the image's spectrum.

    *expected_pitch_um* narrows the search to a factor of two either side; without it the
    search covers pitches from 6 px to a sixth of the image. *kind* ``"hex"`` or
    ``"square"`` requires that lattice; ``"auto"`` decides from the peaks, which for a
    hexagonal lattice come three to a half-plane at 60 degrees from each other and for a
    square one two at 90. Failure carries a reason and no numbers.
    """
    from scipy import ndimage
    from scipy.fft import rfft2

    if kind not in {"hex", "square", "auto"}:
        return LatticeEstimate(False, "kind must be hex, square or auto")
    image = np.asarray(image, dtype=np.float64)
    if image.ndim != 2 or min(image.shape) < 64 or not np.isfinite(image).all():
        return LatticeEstimate(False, "The image must be a finite 2-D array of at least 64 x 64 px")
    if not math.isfinite(pixel_size_um) or pixel_size_um <= 0:
        return LatticeEstimate(False, "pixel_size_um must be finite and positive")
    h, w = image.shape
    if expected_pitch_um is not None:
        pitch_px = expected_pitch_um / pixel_size_um
        pitch_range = (0.5 * pitch_px, 2.0 * pitch_px)
    else:
        pitch_range = (6.0, min(h, w) / 6.0)
    centred = image - image.mean()
    windowed = centred * np.outer(np.hanning(h), np.hanning(w))
    power = np.abs(rfft2(windowed)) ** 2
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.rfftfreq(w)[None, :]
    radius = np.hypot(fy, fx)
    # First-order peaks sit at 1/pitch (square) or 2/(sqrt(3) pitch) (hex).
    ring = (radius >= 1.0 / pitch_range[1]) & (radius <= 2.0 / (math.sqrt(3) * pitch_range[0]))
    if not ring.any():
        return LatticeEstimate(False, "The pitch range leaves no frequencies to search")
    floor = float(np.median(power[ring])) or 1e-30
    local_max = (power == ndimage.maximum_filter(power, size=5)) & ring
    candidates = np.argwhere(local_max & (power > 20 * floor))
    if len(candidates) < 2:
        return LatticeEstimate(False, "No lattice peaks stand out of the spectrum: the image holds "
                                      "no periodic structure at the expected pitch")
    order = np.argsort(power[tuple(candidates.T)])[::-1][:24]
    refined = []
    for index in candidates[order]:
        y, x = int(index[0]), int(index[1])
        ry, rx, rp = _zoom_peak(windowed, float(fy[y, 0]), float(fx[0, x]), 0.1)
        refined.append((np.array([rx, ry]), rp))

    def angle_between(a, b):
        cosine = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
        return math.degrees(math.acos(np.clip(abs(cosine), 0, 1)))        # modulo 180: 0..90

    def family(anchor, minimum_hex_partners) -> tuple[str, list] | None:
        """The kind and partner peaks of the lattice whose first order contains *anchor*, or None."""
        same_ring = [v for v, _ in refined
                     if v is not anchor and abs(np.linalg.norm(v) / np.linalg.norm(anchor) - 1) <= _PEAK_MAGNITUDE_TOLERANCE]
        at_60 = [v for v in same_ring if abs(angle_between(anchor, v) - 60) <= _PEAK_ANGLE_TOLERANCE_DEG]
        at_90 = [v for v in same_ring if abs(angle_between(anchor, v) - 90) <= _PEAK_ANGLE_TOLERANCE_DEG]
        if kind == "hex" or (kind == "auto" and at_60 and len(at_60) >= len(at_90)):
            return ("hex", at_60) if len(at_60) >= minimum_hex_partners else None
        if kind == "square" or (kind == "auto" and at_90):
            return ("square", at_90) if at_90 else None
        return None

    strongest, strongest_power = refined[0]
    chosen = family(strongest, 1)
    if chosen is None:
        return LatticeEstimate(False, "The spectrum's peaks are neither hexagonal nor square")
    found, partners = chosen
    # A lattice's harmonics are peaks too, and a high-pass image (a halo, a flattened background) can
    # make one stronger than the fundamental: a pitch of 172 read as 65 (172 / sqrt 7). A harmonic is
    # an integer combination of the fundamental's reciprocal vectors, so if a lower-frequency family
    # with real power (two partners for a hexagonal one, as chance peaks do not have) has the strongest
    # peak on its reciprocal lattice, the strongest was a harmonic and the lowest such family is the
    # lattice. A lower family that does not explain the strongest peak is not used: a spurious one
    # chosen only for being lowest put the pitch 3.6 % off.
    for vector, vector_power in sorted(refined, key=lambda item: float(np.linalg.norm(item[0]))):
        if (np.linalg.norm(vector) >= 0.9 * np.linalg.norm(strongest)
                or vector_power < _FUNDAMENTAL_POWER_FRACTION * strongest_power):
            continue
        lower = family(vector, 2)
        if lower is None:
            continue
        basis_rows = np.array([vector, lower[1][0]])
        if abs(np.linalg.det(basis_rows)) < 1e-12:
            continue
        coordinates = strongest @ np.linalg.inv(basis_rows)
        if np.all(np.abs(coordinates - np.round(coordinates)) < 0.1):
            strongest, strongest_power, (found, partners) = vector, vector_power, lower
            break
    reciprocal = np.array([strongest, partners[0]])                       # rows: g1, g2 in cycles/px
    if abs(np.linalg.det(reciprocal)) < 1e-12:
        return LatticeEstimate(False, "The spectrum's peaks are collinear")
    real = np.linalg.inv(reciprocal)                                      # columns: a1, a2 in px
    a1, a2 = real[:, 0], real[:, 1]
    shortest = min((a1, a2, a1 + a2, a1 - a2), key=lambda v: float(np.linalg.norm(v)))
    pitch_px = float(np.linalg.norm(shortest))
    period = 60.0 if found == "hex" else 90.0
    angle = math.degrees(math.atan2(-shortest[1], shortest[0]))
    angle = (angle + period / 2) % period - period / 2
    return LatticeEstimate(True, "", found, pitch_px * pixel_size_um, angle,
                           strongest_power / floor, 1 + len(partners))


# ── N2: candidate sites by matched filter ────────────────────────────────────
@dataclass(frozen=True)
class SiteDetection:
    ok: bool
    reason: str
    points_um: np.ndarray | None = None         # (n, 2) centres of the candidates, image frame
    response: np.ndarray | None = None          # matched-filter value at each
    fit: LatticeFit | None = None               # fit_lattice on points_um, in um
    boundary_centre_um: tuple[float, float] | None = None
    boundary_radius_um: float | None = None


def disk_template(radius_px: float, *, ring: float = 1.45) -> np.ndarray:
    """A zero-mean, unit-norm template: a light disk inside a dark annulus.

    The annulus (out to *ring* radii) makes the response fall for a bright region wider than a
    nanowell, so the bright lattice-free floor and a bubble do not read as sites.
    """
    half = int(math.ceil(radius_px * ring)) + 1
    y, x = np.mgrid[-half:half + 1, -half:half + 1]
    distance = np.hypot(y, x)
    inside = distance <= radius_px
    annulus = (distance > radius_px) & (distance <= radius_px * ring)
    template = np.zeros_like(distance)
    template[inside] = 1.0 / inside.sum()
    template[annulus] = -1.0 / annulus.sum()
    return template / np.linalg.norm(template)


def detect_sites(image, pixel_size_um: float, nanowell_diameter_um: float, *,
                 expected_pitch_um: float, kind: str = "hex") -> SiteDetection:
    """Candidate sites, the lattice fitted to them, and the array's boundary.

    The matched filter is a light disk at the plate format's radius; local maxima one
    half-pitch apart whose response clears half of the strong ones' are candidates. A site
    under a bubble responds weakly and is not a candidate, which is correct: it is predicted
    from the lattice later and never dropped.
    """
    from scipy import ndimage, signal

    image = np.asarray(image, dtype=np.float64)
    if image.ndim != 2 or not np.isfinite(image).all():
        return SiteDetection(False, "The image must be a finite 2-D array")
    if not all(math.isfinite(v) and v > 0 for v in (pixel_size_um, nanowell_diameter_um, expected_pitch_um)):
        return SiteDetection(False, "pixel size, nanowell diameter and pitch must be finite and positive")
    radius_px = nanowell_diameter_um / 2 / pixel_size_um
    pitch_px = expected_pitch_um / pixel_size_um
    if radius_px < 2.5:
        return SiteDetection(False, f"A nanowell is {2 * radius_px:.1f} px across at this pixel size: too "
                                    "few pixels to find or fit it")
    template = disk_template(radius_px)
    response = signal.fftconvolve(image - np.median(image), template[::-1, ::-1], mode="same")
    size = max(3, int(round(pitch_px * 0.5)) | 1)
    peaks = (response == ndimage.maximum_filter(response, size=size)) & (response > 0)
    coords = np.argwhere(peaks)
    if len(coords) < 6:
        return SiteDetection(False, "Fewer than six candidate sites were found")
    values = response[tuple(coords.T)]
    keep = values >= 0.5 * _site_response(values)
    coords, values = coords[keep], values[keep]
    centres = np.empty((len(coords), 2))
    for n, (y, x) in enumerate(coords):
        centres[n] = _parabolic_centre(response, int(y), int(x))
    points_um = (centres + 0.5) * pixel_size_um               # array index -> the um frame (pixel centres)
    fit = fit_lattice(points_um, expected_pitch=expected_pitch_um, kind=kind)
    if not fit.ok:
        return SiteDetection(False, f"The lattice could not be fitted to the candidates: {fit.reason}",
                             points_um, values, fit)
    ideal = 60.0 if fit.kind == "hex" else 90.0
    if abs((fit.length_ratio or 1.0) - 1.0) > _MAX_BASIS_DISTORTION or abs(fit.basis_angle_deg - ideal) > 5.0:
        return SiteDetection(
            False, f"The fitted lattice is stretched (basis length ratio {fit.length_ratio:.3f}, angle "
                   f"{fit.basis_angle_deg:.1f} degrees against {ideal:g}): anisotropic pixels or optics, or a "
                   "wrong lattice kind. Pitch, angle and every address would be wrong", points_um, values, fit)
    inliers = points_um[np.asarray(fit.inlier_mask, dtype=bool)]
    boundary = fit_array_boundary(inliers)
    centre = tuple(boundary.centre) if boundary.status == "fitted" else None
    radius = boundary.radius if boundary.status == "fitted" else None
    return SiteDetection(True, "", points_um, values, fit, centre, radius)


def _site_response(values) -> float:
    """The typical response of a real site among local maxima that include noise ones.

    In a small array most local maxima of the filtered image are noise outside it, so a
    percentile of all of them sits in the noise (a 199-site array in a 1500 px image gave 3,147
    candidates and no lattice fit). The maxima split into two populations on a log scale; when
    the split is clear (the upper class's mean is more than twice the lower's) the typical
    site is the upper class's median, and otherwise every maximum is a site and it is the median.
    """
    values = np.sort(np.asarray(values, dtype=float))
    logs = np.log(values)
    edges = np.linspace(logs[0], logs[-1] + 1e-9, 65)
    best = None
    for cut in edges[1:-1]:
        low, high = logs < cut, logs >= cut
        if low.sum() < 3 or high.sum() < 3:
            continue
        weight = low.mean() * high.mean() * (logs[high].mean() - logs[low].mean()) ** 2
        if best is None or weight > best[0]:
            best = (weight, cut)
    if best is None:
        return float(np.median(values))
    upper = values[logs >= best[1]]
    lower = values[logs < best[1]]
    if upper.mean() > 2.0 * lower.mean():
        return float(np.median(upper))
    return float(np.median(values))


def _parabolic_centre(response, y: int, x: int) -> tuple[float, float]:
    """Sub-pixel position (x, y) of a response peak from a parabola through its neighbours."""
    h, w = response.shape
    out = [float(x), float(y)]
    if 0 < x < w - 1:
        a, b, c = response[y, x - 1], response[y, x], response[y, x + 1]
        denom = a - 2 * b + c
        if denom < 0:
            out[0] += 0.5 * (a - c) / denom
    if 0 < y < h - 1:
        a, b, c = response[y - 1, x], response[y, x], response[y + 1, x]
        denom = a - 2 * b + c
        if denom < 0:
            out[1] += 0.5 * (a - c) / denom
    return out[0], out[1]


# ── N3 / N3a: the central square ─────────────────────────────────────────────
@dataclass(frozen=True)
class SquareScore:
    """How square one site's outline is, and which way its edges run."""
    position_um: tuple[float, float]
    coherence: float                    # squareness: near 0 for a circle or a single straight edge, near 1 for a square
    orientation_deg: float              # edge direction modulo 90, in (-45, 45], geometry convention
    straightness: float = 0.0           # the two-fold sum: high where one edge direction dominates


def square_score(image, pixel_size_um: float, nanowell_diameter_um: float, position_um) -> SquareScore | None:
    """The four-fold gradient coherence of the outline around *position_um*.

    Every outline pixel's gradient direction ``a`` contributes ``exp(4ia)`` weighted by its
    magnitude. A circle spreads its gradients over every direction and cancels; a square
    concentrates them on two directions 90 degrees apart and adds. The argument of the sum is
    four times the edge direction, so the same number gives the orientation. ``None`` when
    the patch is off the image or has no gradient at all.
    """
    from scipy import ndimage

    r = nanowell_diameter_um / 2 / pixel_size_um
    cx, cy = position_um[0] / pixel_size_um - 0.5, position_um[1] / pixel_size_um - 0.5      # array index
    half = int(math.ceil(1.5 * r)) + 2
    x0, y0 = int(round(cx)) - half, int(round(cy)) - half
    h, w = np.asarray(image).shape
    if x0 < 0 or y0 < 0 or x0 + 2 * half + 1 > w or y0 + 2 * half + 1 > h:
        return None
    patch = ndimage.gaussian_filter(np.asarray(image, dtype=np.float64)[y0:y0 + 2 * half + 1,
                                                                         x0:x0 + 2 * half + 1], 1.0)
    gy, gx = np.gradient(patch)
    yy, xx = np.mgrid[0:2 * half + 1, 0:2 * half + 1]
    rho = np.hypot(xx + x0 - cx, yy + y0 - cy)
    weight = np.hypot(gx, gy) * ((rho >= 0.6 * r) & (rho <= 1.5 * r))
    total = float(weight.sum())
    if total <= 0:
        return None
    angle = np.arctan2(gy, gx)
    z = complex(np.sum(weight * np.exp(4j * angle)))
    z2 = complex(np.sum(weight * np.exp(2j * angle)))
    orientation = -math.degrees(math.atan2(z.imag, z.real)) / 4          # image y is down: flip the sign
    orientation = (orientation + 45.0) % 90.0 - 45.0
    # A straight edge (a bubble's rim crossing a circle) has its gradients in one direction, so its
    # four-fold sum is as large as a square's; but it has a two-fold sum too, and a square's two
    # edges 90 degrees apart cancel in that. Squareness is the four-fold sum beyond the two-fold one.
    return SquareScore((float(position_um[0]), float(position_um[1])), max(abs(z) - abs(z2), 0.0) / total,
                       orientation, abs(z2) / total)


#: A fiducial must be at least this square in absolute terms, whatever its margin over the runner-up:
#: a clean square scores about 0.74 and a circle near 0, and a margin alone would anchor the well on
#: the least circular of a set of circles.
MIN_SQUARENESS = 0.4


def score_fiducial(image, pixel_size_um: float, nanowell_diameter_um: float, points_um, *,
                   centre_um=None, search_radius_um: float | None = None,
                   min_squareness: float = MIN_SQUARENESS
                   ) -> tuple[FiducialCandidate | None, list[SquareScore]]:
    """Square-versus-circle score of every candidate near the array centre, and the best one.

    Returns ``(candidate, scores)``. The candidate carries the best site's score and the
    runner-up's, for ``resolve_origin``'s margin test; the margin is the caller's, not
    calibrated here. ``None`` with fewer than two scorable sites, because a margin over
    nothing is not a margin, and ``None`` when even the best is under *min_squareness*. Every score is returned so that it can be recorded.
    """
    points = np.asarray(points_um, dtype=float)
    if centre_um is not None and search_radius_um is not None:
        points = points[np.hypot(points[:, 0] - centre_um[0], points[:, 1] - centre_um[1]) <= search_radius_um]
    scores = [s for p in points
              if (s := square_score(image, pixel_size_um, nanowell_diameter_um, p)) is not None]
    if len(scores) < 2:
        return None, scores
    ranked = sorted(scores, key=lambda s: s.coherence, reverse=True)
    if ranked[0].coherence < min_squareness:
        return None, scores
    return FiducialCandidate(ranked[0].position_um, ranked[0].coherence, ranked[1].coherence), scores


@dataclass(frozen=True)
class OrientationCheck:
    ok: bool
    reason: str
    expected_deg: float | None = None
    observed_deg: float | None = None
    discrepancy_deg: float | None = None


def _wrap(angle: float, period: float) -> float:
    return (angle + period / 2) % period - period / 2


def check_orientation(fit: LatticeFit, square_orientation_deg: float, *,
                      square_to_axis_deg: float = 0.0,
                      tolerance_deg: float = SQUARE_AXIS_TOLERANCE_DEG) -> OrientationCheck:
    """Refuse a lattice axis that the square's own edges contradict (tracker N3a).

    The hexagonal lattice repeats every 60 degrees and the square every 90, so together they
    fix the axis up to 180, which a keyed plate never reaches. ``fit_lattice`` picks the axis
    nearest east, and a true rotation of 35 degrees folds back to -25 on the wrong axis; the
    square's edges, measured independently, still read 35. *square_to_axis_deg* is the angle
    from the lattice axis to the square's edge on the mask; **0 is an assumption** (edges
    parallel to a lattice row) that real data has to confirm.
    """
    if not fit.ok:
        return OrientationCheck(False, f"No lattice fit to check: {fit.reason}")
    expected = _wrap(fit.angle_deg + square_to_axis_deg, 90.0)
    observed = _wrap(square_orientation_deg, 90.0)
    discrepancy = _wrap(observed - expected, 90.0)
    if abs(discrepancy) > tolerance_deg:
        return OrientationCheck(
            False, f"The square's edges run at {observed:.1f} degrees but the lattice axis the fit chose "
                   f"({fit.angle_deg:.1f}) predicts {expected:.1f}: the rotation has most likely folded "
                   f"across the ±25 / ±40 degree limit, so addresses on this axis would be wrong",
            expected, observed, discrepancy)
    return OrientationCheck(True, "", expected, observed, discrepancy)


# ── N4: per-site centre and radius ───────────────────────────────────────────
@dataclass(frozen=True)
class SiteGeometry:
    index: int                          # the site's position in the input list
    centre_um: tuple[float, float]
    radius_um: float
    geometry: str                       # FITTED or PREDICTED
    rms_um: float | None = None         # the fit's radial scatter; None when predicted
    rays_used: int = 0


def _row_means(rows):
    """Each row's ``.mean()``, bit for bit, computed for every row at once.

    ``rows.mean(axis=1)`` matches that only on a C-contiguous array: numpy then
    reduces each row as it reduces a 1-D array. Selecting columns with a boolean
    mask returns a Fortran-ordered copy, and an axis mean over that sums column
    by column, which changes the last bit. These means decide ``drop >=
    contrast`` for each ray, so a last-bit change could flip a borderline edge.
    ``tests/test_nanowell_edge_points_vectorised.py`` holds this equal to the
    per-row ``.mean()`` on Fortran-ordered input of every length up to 200.
    """
    return np.ascontiguousarray(rows, dtype=np.float64).mean(axis=1)


def _edge_points(image, centre_px, radius_px, contrast):
    """Edge position along each of _RAYS rays: where brightness falls fastest going outward."""
    from scipy import ndimage

    angles = np.linspace(0, 2 * np.pi, _RAYS, endpoint=False)
    rhos = np.linspace(0.45 * radius_px, 1.7 * radius_px, 64)
    xs = centre_px[0] + np.outer(np.cos(angles), rhos)
    ys = centre_px[1] + np.outer(np.sin(angles), rhos)
    profile = ndimage.map_coordinates(image, [ys, xs], order=1, mode="nearest")
    step = rhos[1] - rhos[0]                                         # px per sample along a ray
    profile = ndimage.gaussian_filter1d(profile, 1.0 / step, axis=1)     # one pixel of smoothing
    slope = np.gradient(profile, rhos, axis=1)
    peak = np.argmin(slope, axis=1)                                  # steepest fall outward
    inner_zone = rhos <= 0.7 * radius_px                              # inside the disk, away from its rim
    outer_zone = rhos >= 1.3 * radius_px                              # the lattice just outside it
    # Every ray at once: the same arithmetic as a per-ray loop, element for element, without
    # two small-array means per ray (refine_sites calls this up to three times per site, and
    # a plate has thousands of sites).
    ok = (peak >= 2) & (peak <= len(rhos) - 3)
    rays = np.flatnonzero(ok)
    k = peak[rays]
    a, b, c = slope[rays, k - 1], slope[rays, k], slope[rays, k + 1]
    denom = a - 2 * b + c
    with np.errstate(divide="ignore", invalid="ignore"):
        shift = np.where(denom > 0, 0.5 * (a - c) / denom * step, 0.0)
    rho = rhos[k] + shift
    # The contrast an edge must show is the disk's plateau over the lattice's, not the drop across
    # a pixel or two: a defocused or haloed rim has a soft, wide edge and the same plateaus.
    drop = _row_means(profile[rays][:, inner_zone]) - _row_means(profile[rays][:, outer_zone])
    # The edge's width in pixels, as its plateau-to-plateau drop over its steepest slope: the
    # measure of how soft this rim is, which sets how precisely its position can be read.
    width = drop / np.maximum(-b, 1e-9)
    keep = drop >= contrast
    return [(angles[r], float(p), float(w))
            for r, p, w in zip(rays[keep], rho[keep], width[keep])]


def _circle_fit(points):
    """Algebraic (Kasa) circle fit: centre and radius of the least-squares circle."""
    x, y = points[:, 0], points[:, 1]
    design = np.column_stack((2 * x, 2 * y, np.ones(len(x))))
    solution = np.linalg.lstsq(design, x ** 2 + y ** 2, rcond=None)[0]
    centre = solution[:2]
    radius = math.sqrt(max(solution[2] + centre @ centre, 0.0))
    return centre, radius


def refine_sites(image, pixel_size_um: float, sites_um, nominal_diameter_um: float, *,
                 relaxed=(), contrast: float | None = None) -> list[SiteGeometry]:
    """Fit every site's centre and radius from its radial edge profile; never drop one.

    A site whose fit holds up (enough rays find an edge, the circle through them has small
    scatter (6 % of the radius plus a quarter of the rim's width), a radius within 30 % of nominal, a centre within a radius of the lattice
    position) is ``fitted``. Any other site takes the lattice position and the well's
    median fitted radius and is ``predicted``: a bubble, debris or a clipped edge costs a
    site its measurement, not its row. *relaxed* lists sites whose outline is not a circle
    (the square) and may scatter more. *contrast* is the intensity drop an edge must show;
    it defaults to 40 % of the image's own nanowell-to-lattice range.
    """
    image = np.asarray(image, dtype=np.float64)
    nominal_px = nominal_diameter_um / 2 / pixel_size_um
    if nominal_px < MIN_REFINE_RADIUS_PX:
        raise ValueError(f"A nanowell is {nominal_px:.1f} px in radius at this pixel size; per-site fitting "
                         f"needs at least {MIN_REFINE_RADIUS_PX:g} px (use a finer image for refinement)")
    if contrast is None:
        contrast = 0.4 * float(np.percentile(image, 98) - np.percentile(image, 5))
    relaxed = set(relaxed)
    fits: list[SiteGeometry | None] = []
    for index, site in enumerate(np.asarray(sites_um, dtype=float)):
        centre = np.array([site[0], site[1]]) / pixel_size_um - 0.5            # array index
        radius = nominal_px
        accepted = None
        for _ in range(3):
            edges = _edge_points(image, centre, radius, contrast)
            if len(edges) < max(int(_MIN_RAY_FRACTION * _RAYS), 6):
                break
            pts = np.array([(centre[0] + rho * math.cos(a), centre[1] + rho * math.sin(a))
                            for a, rho, _ in edges])
            softness = float(np.median([width for _, _, width in edges]))
            new_centre, new_radius = _circle_fit(pts)
            residual = np.abs(np.linalg.norm(pts - new_centre, axis=1) - new_radius)
            keep = residual <= max(2.5 * 1.4826 * np.median(residual), 0.02 * new_radius)
            if keep.sum() < max(int(_MIN_RAY_FRACTION * _RAYS), 6):
                break
            new_centre, new_radius = _circle_fit(pts[keep])
            rms = float(np.sqrt(np.mean((np.linalg.norm(pts[keep] - new_centre, axis=1) - new_radius) ** 2)))
            centre, radius = new_centre, new_radius
            accepted = (rms, int(keep.sum()), softness)
        # Scatter is held to 6 % of the radius plus a quarter of the rim's width beyond a sharp edge's:
        # a defocused rim is a soft edge whose position is read less precisely, but a garbage fit (a bubble's rim, a
        # neighbour) scatters by a large part of the radius whatever the softness.
        limit = (0.2 * radius if index in relaxed
                 else 0.06 * radius + (0.25 * max(accepted[2] - _SHARP_EDGE_WIDTH_PX, 0.0) if accepted else 0.0))
        if (accepted is not None and accepted[0] <= limit
                and abs(radius - nominal_px) <= 0.3 * nominal_px
                and np.linalg.norm(centre - (np.array(site) / pixel_size_um - 0.5)) <= nominal_px):
            fits.append(SiteGeometry(index, (float((centre[0] + 0.5) * pixel_size_um),
                                             float((centre[1] + 0.5) * pixel_size_um)),
                                     float(radius * pixel_size_um), FITTED,
                                     float(accepted[0] * pixel_size_um), accepted[1]))
        else:
            fits.append(None)
    measured = [f.radius_um for f in fits if f is not None and f.index not in relaxed]
    fallback = float(np.median(measured)) if measured else nominal_diameter_um / 2
    return [f if f is not None else SiteGeometry(i, (float(s[0]), float(s[1])), fallback, PREDICTED)
            for i, (f, s) in enumerate(zip(fits, np.asarray(sites_um, dtype=float)))]
