"""Finding a nanowell array from its contrast, and inferring its geometry from where the wells are (tracker X35).

Qt-free; numpy, scipy and scikit-image. The one fact this module relies on is the plate's polarity: **nanowells
are light and the lattice between them is dark**, at every timepoint, on every plate. A bubble over a nanowell
is the exception, and it makes that nanowell dark too, so it is simply not found here; it is never mistaken for
a nanowell. Nothing else about the plate is assumed: not the lattice kind, not the pitch, not the nanowell
diameter, not the array's outline.

1. :func:`find_wells_by_contrast` separates light from dark and keeps the light blobs that look like one well:
   a first, global split (Otsu on the image with its slowest variation removed) finds enough of them to measure
   their spacing, and a second, local split (each pixel against the midpoint of the brightest and darkest within
   a few spacings) follows uneven illumination and leaves out flat regions, such as the floor outside the array
   or a bubble, where there is no well-to-lattice contrast to split. A well cut by the edge of the image is kept
   apart: its centroid is biased, so it says where the array runs off the image and takes no part in any fit.
2. :func:`infer_array` fits the lattice to those positions (``nanowell_lattice.fit_lattice`` with
   ``kind="auto"``: the nearest-neighbour directions repeat every 60 degrees on a hexagonal lattice and every 90
   on a square one), gives every found well its integer lattice coordinates, and reads the array's extent from
   the occupied coordinates: every lattice site inside their convex hull belongs to the array, found or not, so a
   nanowell under a bubble keeps its place and is reported as not found. The outline is a ``circle`` when a
   circle through the outermost wells holds no imaged lattice site the wells do not, and otherwise the hull
   itself (``polygon``).

Positions in micrometres are in the frame where pixel ``i`` covers ``[i, i + 1) * pixel_size``, as in
``pipeline.nanowell_detect``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from pipeline.nanowell_lattice import LatticeFit, fit_array_boundary, fit_lattice, index_points

ARRAY_CIRCLE = "circle"
ARRAY_POLYGON = "polygon"

#: A light blob is one nanowell when its area is within these factors of the typical blob's: smaller is noise or a
#: fragment, larger is wells run together (or a bright region that is not a well).
_AREA_RANGE = (0.45, 1.7)
#: ... and when it is not drawn out: the square root of its second moments' ratio. A circle and the central
#: square are 1.0; two wells run together are about 2.
_MAX_ELONGATION = 1.6
#: Fewer pixels than this cannot be told from noise.
_MIN_AREA_PX = 6
#: The local split looks this many well spacings around each pixel for the brightest and darkest.
_WINDOW_SPACINGS = 2.5
#: Pass 2's typical nanowell is read from round blobs within these factors of pass 1's nanowell area.
_NEAR_PASS_ONE = (0.5, 2.0)
#: The second split smooths the image over this share of a well's diameter first.
_SMOOTH_WELLS = 0.12
#: A blob is a nanowell only if it is lighter than the dark around it by at least this share of the typical blob's
#: lift, and by this many times the noise left after smoothing: speckle of the flat floor outside the array, or of a
#: bubble, is no lighter than its surround beyond the noise, while a nanowell is lighter by the lattice's contrast,
#: which uneven illumination can scale down (a tile a third as bright as its neighbour) but not to the noise.
_MIN_LIFT = 0.2
_MIN_LIFT_NOISE = 8.0
#: A given pitch the measured one departs from by more than this share is reported as a disagreement.
PITCH_AGREEMENT = 0.05
#: The same for the nanowell diameter, which a threshold reads less precisely than a lattice fit reads a pitch.
DIAMETER_AGREEMENT = 0.25
#: A circle is the array's outline only if it holds at most this many imaged lattice sites (or this share of the
#: wells found, if larger) that the found wells' hull does not: a hexagonal outline has whole caps of them.
_CIRCLE_EXTRA_SITES = 2
_CIRCLE_EXTRA_SHARE = 0.03
#: A site the image does not show wholly belongs to a circular array when it lies within the circle plus this
#: share of a pitch (the rim fit's own scatter); one the image does show is in the array only if it was seen.
_RIM_TOLERANCE = 0.02
#: How far a fitted basis may depart from an ideal lattice before it is refused (as ``nanowell_detect``).
_MAX_BASIS_DISTORTION = 0.05


# ── 1. light from dark ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class ContrastWells:
    """Light blobs that look like one nanowell each."""
    ok: bool
    reason: str
    points_um: np.ndarray | None = None          # (n, 2) centroids of whole wells, um frame
    diameter_um: float | None = None             # median equivalent diameter of the whole wells
    edge_points_um: np.ndarray | None = None     # (m, 2) centroids of wells the image's edge cuts (biased)
    rejected: dict = field(default_factory=dict)  # why other light blobs were not taken: reason -> count


def _components(mask: np.ndarray, edge: np.ndarray, values: np.ndarray):
    """Area, centroid (x, y in px), elongation, whether it touches *edge*, and mean of *values*, per component."""
    from scipy import ndimage

    labels, n = ndimage.label(mask)
    if n == 0:
        empty = np.zeros(0)
        return empty, np.zeros((0, 2)), empty, np.zeros(0, dtype=bool), empty
    # Sums over the labelled pixels only: index grids over a whole 4096-pixel montage cost ~1 GB (review X35-R8).
    ys, xs = np.nonzero(labels)
    flat = labels[ys, xs]
    values = values[ys, xs].astype(np.float64)
    ys, xs = ys.astype(np.float64), xs.astype(np.float64)

    def total(weights=None):
        return np.bincount(flat, weights=weights, minlength=n + 1)[1:]

    area = total()
    mx, my = total(xs) / area, total(ys) / area
    sxx = total(xs * xs) / area - mx ** 2
    syy = total(ys * ys) / area - my ** 2
    sxy = total(xs * ys) / area - mx * my
    spread = np.sqrt(np.maximum(((sxx - syy) / 2) ** 2 + sxy ** 2, 0.0))
    major, minor = (sxx + syy) / 2 + spread, (sxx + syy) / 2 - spread
    elongation = np.sqrt(major / np.maximum(minor, 1e-9))
    touching = np.zeros(n + 1, dtype=bool)
    touching[np.unique(labels[edge & (labels > 0)])] = True
    return area, np.column_stack((mx, my)), elongation, touching[1:], total(values) / area


def _fill_small_holes(mask: np.ndarray, max_area: float) -> np.ndarray:
    """*mask* with its holes up to *max_area* pixels filled (a cell darkening a nanowell's middle). Larger holes stay:
    a ring of light speckle on the floor round the array encloses the whole array, and filling every hole made it one
    component the size of the array (review X35-R2)."""
    from scipy import ndimage

    holes, n = ndimage.label(ndimage.binary_fill_holes(mask) & ~mask)
    if n == 0:
        return mask
    small = np.bincount(holes.ravel(), minlength=n + 1) <= max_area
    small[0] = False
    return mask | small[holes]


def _typical_area(area: np.ndarray) -> float:
    """The area-weighted median: the size most of the light area comes in, which noise specks do not move."""
    order = np.sort(area)
    cumulative = np.cumsum(order)
    return float(order[np.searchsorted(cumulative, cumulative[-1] / 2)])


def _global_split(smooth: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Light pixels by one Otsu split of the image with its slowest variation removed."""
    from scipy import ndimage
    from skimage.filters import threshold_otsu

    # Two passes of a box a third of the image wide approximate a broad Gaussian at a cost that does not grow
    # with its width (a Gaussian this wide was most of the time spent here).
    size = max(3, min(smooth.shape) // 3)
    flat = smooth - ndimage.uniform_filter(ndimage.uniform_filter(smooth, size, mode="nearest"), size,
                                           mode="nearest")
    values = flat[valid]
    if values.size < 16 or float(values.max() - values.min()) <= 0:
        return np.zeros_like(valid)
    return (flat > float(threshold_otsu(values))) & valid


def _local_split(smooth: np.ndarray, valid: np.ndarray, spacing_px: float) -> tuple[np.ndarray, np.ndarray]:
    """Light pixels against the midpoint of their neighbourhood's brightest and darkest, and the darkest."""
    from scipy import ndimage

    window = max(3, int(round(_WINDOW_SPACINGS * spacing_px)) | 1)
    high = ndimage.uniform_filter(ndimage.maximum_filter(smooth, window), window)
    low = ndimage.uniform_filter(ndimage.minimum_filter(smooth, window), window)
    return (smooth > (high + low) / 2) & valid, low


def find_wells_by_contrast(image, pixel_size_um: float, *, covered=None) -> ContrastWells:
    """The light blobs of *image* that look like one nanowell each (see the module docstring).

    *covered* marks the pixels that hold image (a montage's coverage mask); pixels outside it are neither
    light nor dark, and a blob touching them is cut by the edge of what was imaged. Never raises on bad data.
    """
    from scipy import ndimage
    from scipy.spatial import cKDTree

    image = np.asarray(image, dtype=np.float32)          # float32 throughout: a montage is up to 4096 px
    if image.ndim != 2 or min(image.shape) < 16:
        return ContrastWells(False, "The image must be a 2-D array of at least 16 x 16 px")
    if not math.isfinite(pixel_size_um) or pixel_size_um <= 0:
        return ContrastWells(False, "pixel_size_um must be finite and positive")
    valid = np.isfinite(image) if covered is None else (np.asarray(covered, dtype=bool) & np.isfinite(image))
    if valid.sum() < 256:
        return ContrastWells(False, "Too little of the image holds data to find nanowells in")
    filled = np.where(valid, image, float(np.median(image[valid])))
    smooth = ndimage.gaussian_filter(filled, 1.0)
    edge = ndimage.binary_dilation(~valid)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True

    # Pass 1: enough wells to measure how far apart they are and how large they are.
    light = ndimage.binary_fill_holes(_global_split(smooth, valid))
    area, centre, elongation, touching, _ = _components(light, edge, smooth)
    plausible = (area >= _MIN_AREA_PX) & ~touching
    if plausible.sum() < 6:
        return ContrastWells(False, "Fewer than six light blobs stand out of the dark lattice")
    typical = _typical_area(area[plausible])
    keep = plausible & (area >= _AREA_RANGE[0] * typical) & (area <= _AREA_RANGE[1] * typical) \
        & (elongation <= _MAX_ELONGATION)
    if keep.sum() < 6:
        return ContrastWells(False, "Fewer than six light blobs are the size and shape of one nanowell")
    distances, _ = cKDTree(centre[keep]).query(centre[keep], k=2)
    spacing = float(np.median(distances[:, 1]))
    if not spacing > 0:
        return ContrastWells(False, "The light blobs have no spacing")

    # Pass 2: each pixel against its own neighbourhood's bright and dark, on an image smoothed at a fraction of a
    # well (camera noise in a flat region otherwise splits into well-sized speckle), and each blob against the
    # dark around it: a blob of the flat floor outside the array, or of a bubble, is no lighter than its
    # surround however the split fell, and a nanowell is lighter by the lattice's whole contrast.
    well_area = float(np.median(area[keep]))                 # pass 1's nanowell, the size pass 2 is judged by
    well_px = 2.0 * math.sqrt(well_area / math.pi)
    sigma_px = max(1.0, _SMOOTH_WELLS * well_px)
    smooth = ndimage.gaussian_filter(filled, sigma_px)
    # The camera noise left after that smoothing: neighbouring pixels' differences (robustly, since the wells'
    # edges are a small share of them), scaled by how much a Gaussian of sigma_px averages white noise.
    steps = np.abs(np.diff(filled, axis=1))[valid[:, 1:] & valid[:, :-1]]
    noise = 1.4826 * float(np.median(steps)) / math.sqrt(2) / (2 * math.sqrt(math.pi) * sigma_px) if steps.size else 0.0
    light, low = _local_split(smooth, valid, spacing)
    area, centre, elongation, touching, mean = _components(_fill_small_holes(light, well_area), edge, smooth)
    at = np.clip(np.rint(centre).astype(int), 0, np.array(image.shape)[::-1] - 1)
    lift = mean - low[at[:, 1], at[:, 0]]
    big_enough = area >= _MIN_AREA_PX
    whole = big_enough & ~touching
    if whole.sum() < 6:
        return ContrastWells(False, "Fewer than six whole nanowells stand out of their neighbourhood")
    round_ = elongation <= _MAX_ELONGATION
    # The typical nanowell of this pass, from the round blobs near pass 1's size that are lighter than their
    # surround — never from every blob: the local split turns flat floor into large merged components, which pull
    # an area-weighted median of all of them to twice a nanowell and reject the real ones (review X35-R2).
    floor = _MIN_LIFT_NOISE * noise
    near = whole & round_ & (area >= _NEAR_PASS_ONE[0] * well_area) & (area <= _NEAR_PASS_ONE[1] * well_area)
    typical = well_area
    if near.sum() >= 6:
        lit = near & (lift >= max(_MIN_LIFT * float(np.median(lift[near])), floor))
        typical = float(np.median(area[lit])) if lit.sum() >= 6 else well_area
    sized = (area >= _AREA_RANGE[0] * typical) & (area <= _AREA_RANGE[1] * typical)
    shaped = whole & sized & round_
    if shaped.sum() < 6:
        return ContrastWells(False, "Fewer than six light blobs are the size and shape of one nanowell")
    light_enough = lift >= max(_MIN_LIFT * float(np.median(lift[shaped])), floor)
    wells = shaped & light_enough
    cut = big_enough & touching & (area <= _AREA_RANGE[1] * typical) & light_enough
    rejected = {"smaller than a nanowell": int((whole & (area < _AREA_RANGE[0] * typical)).sum()),
                "larger than a nanowell": int((whole & (area > _AREA_RANGE[1] * typical)).sum()),
                "drawn out, not round": int((whole & sized & ~round_).sum()),
                "no lighter than its surround": int((shaped & ~light_enough).sum())}
    if wells.sum() < 6:
        return ContrastWells(False, "Fewer than six light blobs are the size and shape of one nanowell",
                             rejected=rejected)
    to_um = lambda px: (px + 0.5) * pixel_size_um          # noqa: E731 - array index -> the um frame
    diameter = 2.0 * math.sqrt(float(np.median(area[wells])) / math.pi) * pixel_size_um
    return ContrastWells(True, "", to_um(centre[wells]), diameter, to_um(centre[cut]), rejected)


# ── 2. the geometry the wells imply ──────────────────────────────────────────
@dataclass(frozen=True)
class ArrayGeometry:
    """The lattice and the array's extent inferred from the wells found, in the image's um frame."""
    ok: bool
    reason: str
    fit: LatticeFit | None = None
    shape: str | None = None                      # ARRAY_CIRCLE or ARRAY_POLYGON
    #: The array's centre: the circle's centre, or the lattice site at the middle of the polygon's sites. ``None``
    #: for a polygon the image cuts (``clipped``), whose middle is not the array's.
    centre_um: tuple[float, float] | None = None
    radius_um: float | None = None                # the circle's radius (a circle only)
    roster_um: np.ndarray | None = None           # (k, 2) every lattice site of the array, found or not
    found: tuple[bool, ...] = ()                  # per roster site: a whole well was found there
    imaged: tuple[bool, ...] = ()                 # per roster site: a whole well there would lie on the image
    clipped: bool = False                         # the array runs off the image: its extent is a lower bound
    outliers: int = 0                             # wells found off the lattice, given no address

    @property
    def missing(self) -> int:
        """Imaged roster sites where no well was found: occluded (a bubble) or not detected."""
        return sum(1 for f, i in zip(self.found, self.imaged) if i and not f)

    def summary(self) -> dict:
        """The block a run records: what was inferred and how much of it was seen."""
        fit = self.fit
        return {"method": "contrast", "lattice_kind": fit.kind, "pitch_um": fit.pitch, "angle_deg": fit.angle_deg,
                "shape": self.shape, "radius_um": self.radius_um, "clipped": self.clipped,
                "n_sites": len(self.found), "n_found": int(sum(self.found)), "n_missing": self.missing,
                "n_unimaged": int(sum(not i for i in self.imaged)), "n_outliers": self.outliers}


def _inside_hull(candidates: np.ndarray, vertices: np.ndarray, tolerance: float) -> np.ndarray:
    from scipy.spatial import ConvexHull

    equations = ConvexHull(vertices).equations                 # rows: unit normal, offset; inside is <= 0
    return np.all(candidates @ equations[:, :2].T + equations[:, 2] <= tolerance, axis=1)


def _depth_map(covered, pixel_size_um: float) -> np.ndarray:
    """How far each pixel lies inside the imaged area (um), the image's frame counting as an edge."""
    from scipy import ndimage

    padded = np.pad(np.asarray(covered, dtype=bool), 1)
    return ndimage.distance_transform_edt(padded)[1:-1, 1:-1] * pixel_size_um


def _depth_at(points_um: np.ndarray, depth: np.ndarray, pixel_size_um: float) -> np.ndarray:
    """*depth* at each point (um); 0 for a point off the image."""
    index = np.rint(points_um / pixel_size_um - 0.5).astype(int)
    h, w = depth.shape
    on = (index[:, 0] >= 0) & (index[:, 0] < w) & (index[:, 1] >= 0) & (index[:, 1] < h)
    out = np.zeros(len(points_um))
    out[on] = depth[index[on, 1], index[on, 0]]
    return out


def infer_array(points_um, *, pixel_size_um: float, covered, edge_points_um=None, diameter_um: float | None = None,
                kind: str = "auto") -> ArrayGeometry:
    """Lattice, address and extent of the array whose wells are at *points_um* (see the module docstring).

    *covered* is the image's coverage mask (the um frame at *pixel_size_um*), which says which lattice sites
    were imaged at all and whether the array runs off the image. *edge_points_um* are wells the image's edge
    cuts (``ContrastWells.edge_points_um``): one on the lattice is proof that the array continues past the edge.
    A roster site counts as imaged when a disk of *diameter_um* around it lies on the image (its centre, without
    one), so a nanowell the edge cuts is not reported as missing.
    *kind* ``"hex"`` or ``"square"`` requires that lattice; ``"auto"`` decides. Never raises on bad data.
    """
    from scipy.spatial import QhullError

    points = np.asarray(points_um, dtype=float).reshape(-1, 2)
    if len(points) < 6:
        return ArrayGeometry(False, "Fewer than six nanowells to infer a lattice from")
    fit = fit_lattice(points, kind=kind)
    if not fit.ok:
        return ArrayGeometry(False, f"No lattice fits the nanowells found: {fit.reason}", fit)
    ideal = 60.0 if fit.kind == "hex" else 90.0
    if abs((fit.length_ratio or 1.0) - 1.0) > _MAX_BASIS_DISTORTION or abs(fit.basis_angle_deg - ideal) > 5.0:
        return ArrayGeometry(False, f"The lattice fitted to the nanowells is stretched (basis length ratio "
                                    f"{fit.length_ratio:.3f}, angle {fit.basis_angle_deg:.1f} degrees against "
                                    f"{ideal:g}): anisotropic pixels or optics", fit)
    basis = np.column_stack((fit.a1, fit.a2))
    phase = np.asarray(fit.phase_origin)
    indexed = index_points(points, fit, phase)
    occupied = {(p.q, p.r) for p in indexed if not p.outlier}
    if len(occupied) < 6:
        return ArrayGeometry(False, "Fewer than six nanowells lie on the fitted lattice", fit)
    found_qr = np.array(sorted(occupied))
    found_xy = phase + found_qr @ basis.T                     # the lattice's own positions, free of detection noise
    boundary = fit_array_boundary(found_xy)
    # Every lattice site the array could hold: the found sites' extent, widened by the circle's reach when one
    # fits (a circle the image cuts reaches past every site that was seen).
    span = 2 + (int(math.ceil(2 * boundary.radius / fit.pitch)) if boundary.status == "fitted" else 0)
    low, high = found_qr.min(axis=0) - span, found_qr.max(axis=0) + span
    grid = np.array([(q, r) for q in range(low[0], high[0] + 1) for r in range(low[1], high[1] + 1)])
    grid_xy = phase + grid @ basis.T
    try:
        in_hull = _inside_hull(grid_xy, found_xy, fit.pitch / 4)
    except QhullError as exc:                                 # the wells lie on one line
        return ArrayGeometry(False, f"The nanowells found do not span an area: {exc}", fit)
    depth_map = _depth_map(covered, pixel_size_um)
    depth = _depth_at(grid_xy, depth_map, pixel_size_um)
    whole = depth > max((diameter_um or 0.0) / 2, 1e-9)      # a well's whole disk would lie on the image
    edge = np.asarray(edge_points_um if edge_points_um is not None else np.zeros((0, 2)), dtype=float).reshape(-1, 2)
    cut_on_lattice = bool(len(edge)) and any(not p.outlier for p in index_points(edge, fit, phase))
    clipped = cut_on_lattice or bool((_depth_at(found_xy, depth_map, pixel_size_um) < 0.6 * fit.pitch).any())

    shape, centre, radius, members = ARRAY_POLYGON, None, None, in_hull
    if boundary.status == "fitted":
        distance = np.linalg.norm(grid_xy - np.asarray(boundary.centre), axis=1)
        # Sites well inside the circle (a quarter pitch in from it, so a site the rim fit is unsure of does not
        # count) that are wholly on the image and outside the found sites' hull. A circle has none, or a few under
        # a bubble; a hexagonal or square outline has the whole cap between it and its circumcircle.
        extra = int((whole & ~in_hull & (distance <= boundary.radius - fit.pitch / 4)).sum())
        if extra <= max(_CIRCLE_EXTRA_SITES, _CIRCLE_EXTRA_SHARE * len(occupied)):
            shape, centre, radius = ARRAY_CIRCLE, tuple(map(float, boundary.centre)), float(boundary.radius)
            # What was seen, what lies well inside the circle (a bubble over it), and what the image does not show
            # wholly within the circle. A site just past the rim and on the image would have been seen if it were
            # there: the circle's own tolerance would add a ring of sites the plate does not have.
            members = (in_hull | (distance <= radius - fit.pitch / 4)
                       | (~whole & (distance <= radius + _RIM_TOLERANCE * fit.pitch)))
    roster_qr = grid[members]
    if shape == ARRAY_POLYGON and not clipped:
        # The middle of the sites, rounded half down on each axis: the roster is a set of integers, so this is
        # exact and does not move with detection noise. An array with an even number of sites across has two
        # middle candidates, and the rule picks the same one at every timepoint.
        middle = np.ceil(roster_qr.mean(axis=0) - 0.5 - 1e-9).astype(int)
        centre = tuple(map(float, phase + basis @ middle))
    return ArrayGeometry(True, "", fit, shape, centre, radius, grid_xy[members],
                         tuple(tuple(a) in occupied for a in roster_qr), tuple(map(bool, whole[members])),
                         clipped, sum(1 for p in indexed if p.outlier))


def contrast_fit(image, pixel_size_um: float, *, expected_pitch_um: float | None = None,
                 kind: str = "auto") -> LatticeFit | None:
    """The lattice fitted to one image's wells found by contrast, or ``None`` (too few wells, or no fit).

    For registration's per-tile lattice phase: a tile of bare floor or one a bubble covers has no fit and
    contributes the other sources of position.
    """
    wells = find_wells_by_contrast(image, pixel_size_um)
    if not wells.ok:
        return None
    fit = fit_lattice(wells.points_um, expected_pitch=expected_pitch_um, kind=kind)
    return fit if fit.ok else None


def compare_given(measured: float | None, given: float | None, tolerance: float, what: str,
                  consequence: str) -> dict:
    """A measured length against the one the settings give: both, whether they agree, and the sentence if not.

    *consequence* says what the run does with the measured number instead (it differs: a pitch places addresses, a
    diameter only fits and measures them), so the note never claims more than the run does.
    """
    if measured is None or given is None or not given > 0:
        return {"given_um": given, "measured_um": measured, "agrees": None, "note": ""}
    agrees = abs(measured / given - 1.0) <= tolerance
    note = "" if agrees else (
        f"The measured {what} ({measured:.1f} um) disagrees with the {given:g} um the settings give (by "
        f"{(measured / given - 1.0) * 100:+.0f} %, beyond {tolerance * 100:.0f} %): {consequence}; check the plate "
        "format")
    return {"given_um": given, "measured_um": measured, "agrees": agrees, "note": note}
