"""Qt-free geometry for nanowell point sets and tile ownership.

Coordinates use one consistent input unit (pixels or micrometres). Rectangles
use image x/y axes; every site receives an owner, fused index, or unimaged index.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math

import numpy as np

#: The largest rotation from image east, in degrees, at which the nearest-east
#: axis is accepted. A hexagonal lattice looks the same every 60°, a square one
#: every 90°, so two axes are equally near east at 30° / 45° and the choice
#: between them is arbitrary there. It is unreliable well before that: with
#: 2 µm of positional noise a true 29.99° hex rotation was fitted as -30.03°
#: and reported as a success, which turns every address in the well by one
#: sixth of a turn. The 5° margin keeps the choice stable under noise; a fit
#: beyond it is refused rather than relabelled. The lattice alone cannot tell
#: a rotation of θ from θ ± 60° (hex) or ± 90° (square), so this protects only
#: the band next to the ambiguity; a true rotation further out is folded back
#: inside the limit, and the square nanowell's own orientation is what can
#: catch that (tracked as N3a).
_ORIENTATION_LIMIT_DEG = {"hex": 25.0, "square": 40.0}


@dataclass(frozen=True)
class TileRectangle:
    id: str
    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self):
        bounds = (self.left, self.top, self.right, self.bottom)
        if not self.id or not all(math.isfinite(v) for v in bounds):
            raise ValueError("Tiles require an id and finite bounds")
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError(f"Tile {self.id!r} has non-positive extent")


@dataclass(frozen=True)
class OwnershipResult:
    owners: tuple[str | None, ...]
    fused_indices: tuple[int, ...]
    unimaged_indices: tuple[int, ...]


def _disk_covered(x, y, radius, tiles):
    """Test disk coverage exactly by vertical slabs of the rectangle union.

    Tile x edges partition the plane into slabs with constant covered y
    intervals. The disk's widest vertical section in each slab must lie in
    one merged interval; this also detects small interior coverage holes.
    """
    edges = sorted({x - radius, x + radius} | {
        edge for tile in tiles for edge in (tile.left, tile.right)
        if x - radius < edge < x + radius})
    for left, right in zip(edges, edges[1:]):
        middle = (left + right) / 2
        intervals = sorted((tile.top, tile.bottom) for tile in tiles
                           if tile.left <= middle <= tile.right)
        merged = []
        for top, bottom in intervals:
            if merged and top <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(bottom, merged[-1][1]))
            else:
                merged.append((top, bottom))
        distance = max(left - x, x - right, 0)
        height = math.sqrt(max(0, radius ** 2 - distance ** 2))
        if not any(top <= y - height and y + height <= bottom for top, bottom in merged):
            return False
    return True


def assign_owners(site_xy, nanowell_diameter: float, tiles, margin: float) -> OwnershipResult:
    """Assign whole disks with margin; distance ties use lexical tile-id order.

    Unowned sites are fused only if the physical disk is covered by the tile
    union. Any missing part of the disk is explicitly unimaged. The caller's
    margin controls single-tile ownership, not the physical coverage boundary.
    """
    sites = np.asarray(site_xy, dtype=float)
    if sites.ndim != 2 or sites.shape[1] != 2 or not np.isfinite(sites).all():
        raise ValueError("site_xy must be a finite N-by-2 array")
    if not math.isfinite(nanowell_diameter) or nanowell_diameter <= 0:
        raise ValueError("nanowell_diameter must be finite and positive")
    if not math.isfinite(margin) or margin < 0:
        raise ValueError("margin must be finite and non-negative")
    tiles = tuple(tiles)
    if len({tile.id for tile in tiles}) != len(tiles):
        raise ValueError("Tile ids must be unique")
    extent = nanowell_diameter / 2 + margin
    owners = []
    fused = []
    unimaged = []
    for number, (x, y) in enumerate(sites):
        candidates = []
        for tile in tiles:
            if (tile.left <= x - extent and x + extent <= tile.right
                    and tile.top <= y - extent and y + extent <= tile.bottom):
                distance = (x - (tile.left + tile.right) / 2) ** 2
                distance += (y - (tile.top + tile.bottom) / 2) ** 2
                candidates.append((distance, tile.id))
        if candidates:
            owners.append(min(candidates)[1])
        else:
            owners.append(None)
            if _disk_covered(x, y, nanowell_diameter / 2, tiles):
                fused.append(number)
            else:
                unimaged.append(number)
    return OwnershipResult(tuple(owners), tuple(fused), tuple(unimaged))


@dataclass(frozen=True)
class PlateRigidFit:
    ok: bool
    reason: str
    rotation: tuple[tuple[float, float], tuple[float, float]] | None = None
    translation: tuple[float, float] | None = None
    residuals: tuple[tuple[str, float], ...] = ()
    inlier_wells: tuple[str, ...] = ()
    rms_residual: float | None = None
    source_centroid: tuple[float, float] | None = None
    source_spread: float | None = None
    residual_variance: float | None = None


@dataclass(frozen=True)
class OriginPrediction:
    ok: bool
    reason: str
    position: tuple[float, float] | None = None
    uncertainty: float | None = None


def _kabsch(source, target):
    source_mean, target_mean = source.mean(axis=0), target.mean(axis=0)
    u, _s, vt = np.linalg.svd((source - source_mean).T @ (target - target_mean))
    correction = np.diag((1.0, np.linalg.det(vt.T @ u.T)))
    rotation = vt.T @ correction @ u.T
    return rotation, target_mean - rotation @ source_mean


def _rigid_residuals(source, target, rotation, translation):
    return np.linalg.norm(source @ rotation.T + translation - target, axis=1)


def fit_plate_rigid(origins_a, origins_b) -> PlateRigidFit:
    """Robust proper-rotation Kabsch fit, with no fitted scale or reflection.

    Pair hypotheses minimise the median radial residual. Consensus is refitted
    and reselected using a 6-sigma Rayleigh radial cutoff, which avoids
    treating a two-dimensional residual as a signed Gaussian deviation. At
    least three common wells are required. Rejected residuals are also reported.
    """
    wells = sorted(set(origins_a) & set(origins_b))
    if len(wells) < 3:
        return PlateRigidFit(False, "At least three common wells are required")
    source = np.asarray([origins_a[w] for w in wells], dtype=float)
    target = np.asarray([origins_b[w] for w in wells], dtype=float)
    if (source.shape != (len(wells), 2) or target.shape != source.shape
            or not np.isfinite(source).all() or not np.isfinite(target).all()):
        return PlateRigidFit(False, "Well origins must be finite two-dimensional coordinates")
    floor = 64 * np.finfo(float).eps * max(1.0, np.abs(source).max(), np.abs(target).max())
    best = None
    for pair in combinations(range(len(wells)), 2):
        indices = list(pair)
        if np.linalg.norm(source[indices[1]] - source[indices[0]]) <= floor:
            continue
        rotation, translation = _kabsch(source[indices], target[indices])
        residuals = _rigid_residuals(source, target, rotation, translation)
        score = (float(np.median(residuals)), float(np.mean(np.sort(residuals)[:3])))
        if best is None or score < best[0]:
            best = score, residuals
    if best is None:
        return PlateRigidFit(False, "Well origins have no spatial spread")
    residuals = best[1]
    cutoff = max(floor, 6 * np.median(residuals) / math.sqrt(2 * math.log(2)))
    inliers = residuals <= cutoff
    if inliers.sum() < 3:
        return PlateRigidFit(False, "Fewer than three wells support one rigid transform")
    for _ in range(10):
        rotation, translation = _kabsch(source[inliers], target[inliers])
        residuals = _rigid_residuals(source, target, rotation, translation)
        cutoff = max(floor, 6 * np.median(residuals) / math.sqrt(2 * math.log(2)))
        revised = residuals <= cutoff
        if revised.sum() < 3:
            return PlateRigidFit(False, "Fewer than three wells support the refined transform")
        if np.array_equal(revised, inliers):
            break
        inliers = revised
    rotation, translation = _kabsch(source[inliers], target[inliers])
    residuals = _rigid_residuals(source, target, rotation, translation)
    centre = source[inliers].mean(axis=0)
    spread = float(np.sum((source[inliers] - centre) ** 2))
    if spread <= floor ** 2:
        return PlateRigidFit(False, "Consensus well origins have no spatial spread")
    squared_error = float(np.sum(residuals[inliers] ** 2))
    variance = squared_error / (2 * int(inliers.sum()) - 3)
    return PlateRigidFit(
        True, "", tuple(tuple(row) for row in rotation), tuple(translation),
        tuple(zip(wells, map(float, residuals))), tuple(w for w, keep in zip(wells, inliers) if keep),
        math.sqrt(squared_error / int(inliers.sum())), tuple(centre), spread, variance)


def predict_origin(transform: PlateRigidFit, origin_a) -> OriginPrediction:
    """Predict a stage origin and its one-sigma radial uncertainty.

    The uncertainty is the linearised Kabsch prediction standard error under
    independent isotropic residuals: variance * (2/n + distance²/spread). It
    includes rotational leverage away from the anchored wells' centroid. It is
    not a hard error bound and does not include stage calibration uncertainty.
    """
    if not transform.ok:
        return OriginPrediction(False, transform.reason)
    point = np.asarray(origin_a, dtype=float)
    if point.shape != (2,) or not np.isfinite(point).all():
        return OriginPrediction(False, "Prediction origin must be a finite coordinate pair")
    position = np.asarray(transform.rotation) @ point + transform.translation
    distance2 = float(np.sum((point - transform.source_centroid) ** 2))
    leverage = 2 / len(transform.inlier_wells) + distance2 / transform.source_spread
    return OriginPrediction(True, "", tuple(position), math.sqrt(transform.residual_variance * leverage))


@dataclass(frozen=True)
class LatticeFit:
    ok: bool
    reason: str
    a1: tuple[float, float] | None = None
    a2: tuple[float, float] | None = None
    pitch: float | None = None
    angle_deg: float | None = None
    kind: str | None = None
    phase_origin: tuple[float, float] | None = None
    rms_residual: float | None = None
    inlier_mask: tuple[bool, ...] = ()
    basis_angle_deg: float | None = None
    length_ratio: float | None = None
    mirrored: bool = False
    mirror_axis: str | None = None


@dataclass(frozen=True)
class IndexedPoint:
    q: int | None
    r: int | None
    residual: float
    outlier: bool
    reason: str = ""


def _points(value):
    points = np.asarray(value, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        raise ValueError("Points must be a finite N-by-2 array")
    return points


def _coordinate(value):
    point = np.asarray(value, dtype=float)
    if point.shape != (2,) or not np.isfinite(point).all():
        raise ValueError("Position must be a finite coordinate pair")
    return point


def _nearest_sites(points, basis, origin):
    fractional = np.linalg.solve(basis, (points - origin).T).T
    rounded = np.rint(fractional).astype(np.int64)
    offsets = np.array([(q, r) for q in (-1, 0, 1) for r in (-1, 0, 1)])
    candidates = rounded[:, None, :] + offsets
    distances = np.linalg.norm(candidates @ basis.T + origin - points[:, None, :], axis=2)
    best = distances.argmin(axis=1)
    return candidates[np.arange(len(points)), best], distances[np.arange(len(points)), best]


def _unique_inliers(indices, residuals, cutoff):
    keep = np.zeros(len(indices), dtype=bool)
    seen = set()
    for i in np.argsort(residuals, kind="stable"):
        site = tuple(indices[i])
        if residuals[i] <= cutoff and site not in seen:
            keep[i] = True
            seen.add(site)
    return keep


def _seed_basis(points, expected_pitch, kind):
    from scipy.spatial import cKDTree

    distances, neighbours = cKDTree(points).query(points, k=min(7, len(points)))
    nearest = distances[:, 1]
    positive = nearest[nearest > 0]
    if not len(positive):
        raise ValueError("Points have no spatial spread")
    pitch = float(expected_pitch if expected_pitch is not None else np.median(positive))
    vectors = (points[neighbours[:, 1:]] - points[:, None, :]).reshape(-1, 2)
    lengths = np.linalg.norm(vectors, axis=1)
    vectors = vectors[(lengths > 0.8 * pitch) & (lengths < 1.2 * pitch)]
    if len(vectors) < 12:
        raise ValueError("Too few nearest-neighbour vectors support the pitch")
    # Antipodes have the same folded angle. Harmonics cluster two (square) or
    # three (hex) axes without giving reversed vectors separate votes.
    angles = np.arctan2(-vectors[:, 1], vectors[:, 0]) % math.pi
    strengths = {name: np.mean(np.exp(1j * order * angles))
                 for name, order in (("hex", 6), ("square", 4))}
    selected = max(strengths, key=lambda name: abs(strengths[name])) if kind == "auto" else kind
    order = 6 if selected == "hex" else 4
    if abs(strengths[selected]) < 0.65:
        raise ValueError("Nearest-neighbour angles do not support a regular lattice")
    angle = float(np.angle(strengths[selected]) / order)
    if abs(abs(angle) - math.pi / order) < 1e-9:
        raise ValueError("Two lattice directions are equally near east; orientation is ambiguous")
    directions = []
    for target in (angle, angle + 2 * math.pi / order):
        unit = np.array((math.cos(target), -math.sin(target)))
        aligned = vectors * np.where(vectors @ unit >= 0, 1, -1)[:, None]
        departures = np.arctan2(np.abs(np.cross(aligned, unit)), aligned @ unit)
        cluster = aligned[departures < math.radians(15)]
        if len(cluster) < 3:
            raise ValueError("An independent nearest-neighbour direction is missing")
        directions.append(np.median(cluster, axis=0))
    return np.column_stack(directions), selected


def _refine_lattice(points, basis):
    origin = points[np.argmin(np.linalg.norm(points - points.mean(axis=0), axis=1))].copy()
    pitch = np.mean(np.linalg.norm(basis, axis=0))
    fractional = np.linalg.solve(basis, (points - origin).T).T
    phase = np.angle(np.mean(np.exp(2j * math.pi * fractional), axis=0)) / (2 * math.pi)
    origin += basis @ phase
    for _ in range(12):
        indices, residuals = _nearest_sites(points, basis, origin)
        provisional = residuals <= 0.25 * pitch
        if provisional.sum() < 6:
            raise ValueError("Too few points can be indexed cleanly")
        radial = residuals[provisional]
        median = np.median(radial)
        cutoff = min(0.25 * pitch, max(0.01 * pitch, median + 4 * np.median(abs(radial - median))))
        keep = _unique_inliers(indices, residuals, cutoff)
        design = np.column_stack((indices[keep], np.ones(keep.sum())))
        if np.linalg.matrix_rank(design) < 3:
            raise ValueError("Indexed points do not span two dimensions")
        coefficients = np.linalg.lstsq(design, points[keep], rcond=None)[0]
        refined, shifted = coefficients[:2].T, coefficients[2]
        converged = np.max(abs(refined - basis)) < pitch * 1e-9
        converged &= np.linalg.norm(shifted - origin) < pitch * 1e-9
        basis, origin = refined, shifted
        if converged:
            break
    indices, residuals = _nearest_sites(points, basis, origin)
    keep = _unique_inliers(indices, residuals, cutoff)
    if keep.sum() < max(6, math.ceil(0.6 * len(points))):
        raise ValueError("Fewer than sixty percent of points support one lattice")
    rms = float(np.sqrt(np.mean(residuals[keep] ** 2)))
    if rms > 0.1 * pitch:
        raise ValueError("Lattice residuals exceed a tenth of the pitch")
    # A phase is a lattice site, not the observation-window centroid. Rebase
    # the affine fit onto the exact site nearest the retained points' centroid.
    centre = points[keep].mean(axis=0)[None, :]
    nearest, _ = _nearest_sites(centre, basis, origin)
    return basis, origin + basis @ nearest[0], keep, rms


def fit_lattice(points, *, expected_pitch=None, kind="hex", mirrored=False,
                mirror_axis=None) -> LatticeFit:
    """Fit a general affine lattice from clustered nearest-neighbour vectors.

    Screen coordinates have y down. A mirrored camera must explicitly name
    its reflected coordinate (x or y); the nearest-east basis is selected in
    that unmirrored frame and then returned in the input frame. No camera axis
    is inferred. Failure results carry no fabricated basis or phase origin.
    """
    if kind not in {"hex", "square", "auto"}:
        return LatticeFit(False, "Lattice kind must be hex, square, or auto")
    if mirrored and mirror_axis not in {"x", "y"}:
        return LatticeFit(False, "A mirrored camera requires an explicit x or y mirror_axis")
    if not mirrored and mirror_axis is not None:
        return LatticeFit(False, "mirror_axis requires mirrored=True")
    if expected_pitch is not None and (not math.isfinite(expected_pitch) or expected_pitch <= 0):
        return LatticeFit(False, "Expected pitch must be finite and positive")
    try:
        observed = _points(points)
        if len(observed) < 6:
            raise ValueError("At least six lattice points are required")
        reflection = np.diag((-1., 1.) if mirror_axis == "x" else (1., -1.)) if mirrored else np.eye(2)
        unmirrored = observed @ reflection
        basis, selected = _seed_basis(unmirrored, expected_pitch, kind)
        basis, origin, keep, rms = _refine_lattice(unmirrored, basis)
        lengths = np.linalg.norm(basis, axis=0)
        basis_angle = math.degrees(math.acos(np.clip(np.dot(basis[:, 0], basis[:, 1]) / np.prod(lengths), -1, 1)))
        ideal_angle = 60 if selected == "hex" else 90
        if abs(basis_angle - ideal_angle) > 15 or not 0.7 < lengths[1] / lengths[0] < 1.3:
            raise ValueError("Refined basis departs too far from the selected lattice")
        angle = math.degrees(math.atan2(-basis[1, 0], basis[0, 0]))
        limit = _ORIENTATION_LIMIT_DEG[selected]
        # The tolerance only absorbs floating-point error at exactly the limit.
        if abs(angle) > limit + 1e-6:
            raise ValueError(
                f"The lattice is rotated {angle:.2f}° from image east, beyond the "
                f"±{limit:g}° within which its nearest-east axis is unambiguous "
                f"for a {selected} lattice; refusing rather than choosing an axis")
        actual = reflection @ basis
        return LatticeFit(True, "", tuple(actual[:, 0]), tuple(actual[:, 1]),
                          float(lengths.mean()), angle, selected, tuple(reflection @ origin), rms,
                          tuple(map(bool, keep)), basis_angle, float(lengths[1] / lengths[0]),
                          mirrored, mirror_axis)
    except (ValueError, np.linalg.LinAlgError) as error:
        return LatticeFit(False, str(error))


def index_points(points, fit: LatticeFit, origin) -> tuple[IndexedPoint, ...]:
    """Index unique sites within a quarter pitch; rejected points have no address."""
    if not fit.ok:
        raise ValueError(f"Cannot index an unsuccessful lattice fit: {fit.reason}")
    points = _points(points)
    basis = np.column_stack((fit.a1, fit.a2))
    indices, residuals = _nearest_sites(points, basis, _coordinate(origin))
    keep = _unique_inliers(indices, residuals, fit.pitch / 4)
    return tuple(IndexedPoint(int(q), int(r), float(error), False) if retained else
                 IndexedPoint(None, None, float(error), True,
                              "Residual exceeds quarter pitch" if error > fit.pitch / 4 else "Duplicate site")
                 for (q, r), error, retained in zip(indices, residuals, keep))


@dataclass(frozen=True)
class FiducialCandidate:
    position: tuple[float, float]
    score: float
    runner_up_score: float


@dataclass(frozen=True)
class OriginResolution:
    status: str
    origin_xy: tuple[float, float] | None
    method: str | None
    agreement: str
    reason: str


def _snap_origin(fit, position):
    basis = np.column_stack((fit.a1, fit.a2))
    indices, _ = _nearest_sites(_coordinate(position)[None, :], basis, np.asarray(fit.phase_origin))
    return tuple(np.asarray(fit.phase_origin) + basis @ indices[0])


def resolve_origin(fit: LatticeFit, *, fiducial=None, array_centre=None,
                   plate_rigid=None, fiducial_margin) -> OriginResolution:
    """Resolve independent evidence in priority order, with explicit conflicts.

    The caller supplies its score margin; it is not calibrated from synthetic
    data. Plate-rigid uncertainty is one-sigma radial: three sigma must be
    strictly below half a pitch. Every accepted position is snapped to a site.
    """
    if not math.isfinite(fiducial_margin) or fiducial_margin < 0:
        raise ValueError("fiducial_margin must be finite and non-negative")
    if not fit.ok:
        return OriginResolution("unaddressed", None, None, "single_source", fit.reason)
    candidates = []
    if fiducial is not None:
        scores = (fiducial.score, fiducial.runner_up_score)
        if all(math.isfinite(v) for v in scores) and scores[0] - scores[1] > fiducial_margin:
            candidates.append(("fiducial", _snap_origin(fit, fiducial.position)))
    if array_centre is not None:
        candidates.append(("array_centre", _snap_origin(fit, array_centre)))
    if (plate_rigid is not None and plate_rigid.ok and plate_rigid.uncertainty is not None
            and math.isfinite(plate_rigid.uncertainty) and plate_rigid.uncertainty >= 0
            and 3 * plate_rigid.uncertainty < fit.pitch / 2):
        candidates.append(("plate_rigid", _snap_origin(fit, plate_rigid.position)))
    if not candidates:
        return OriginResolution("unaddressed", None, None, "single_source",
                                "No accepted fiducial, array centre, or precise plate-rigid prediction")
    method, origin = candidates[0]
    agreement = "single_source"
    if len(candidates) > 1:
        agreement = "agree" if all(np.linalg.norm(np.subtract(origin, xy)) < fit.pitch / 4
                                   for _, xy in candidates[1:]) else "conflict"
    reason = "Accepted sources disagree; the higher-priority source is retained" if agreement == "conflict" else ""
    return OriginResolution("anchored", origin, method, agreement, reason)


@dataclass(frozen=True)
class ArrayBoundary:
    status: str
    reason: str
    centre: tuple[float, float] | None = None
    radius: float | None = None
    arc_coverage_deg: float | None = None


def _circle_three(points):
    matrix = 2 * (points[1:] - points[0])
    if abs(np.linalg.det(matrix)) < 1e-10:
        raise ValueError("Collinear boundary samples")
    centre = np.linalg.solve(matrix, np.sum(points[1:] ** 2, axis=1) - np.sum(points[0] ** 2))
    return centre, float(np.linalg.norm(points[0] - centre))


def _arc_coverage(points, centre):
    angles = np.sort(np.arctan2(*(points - centre).T[::-1]) % (2 * math.pi))
    gaps = np.diff(np.r_[angles, angles[0] + 2 * math.pi])
    return float(math.degrees(2 * math.pi - gaps.max()))


def _sufficient_arc(coverage, spacing, radius):
    # A discrete rim cannot specify arc endpoints more finely than one site.
    resolution = math.degrees(2 * math.asin(min(1.0, spacing / (2 * radius))))
    return coverage + resolution >= 120


def fit_array_boundary(site_xy) -> ArrayBoundary:
    """Robust circle through convex-hull sites, including a partial boundary arc.

    Input sites must already be lattice-filtered. RANSAC distinguishes the
    circular rim from window chords; interior detections do not define a rim.
    The accepted radial band is half a nearest-neighbour spacing, reflecting
    the discrete lattice boundary. Coverage is measured from retained rim sites;
    the 120-degree acceptance threshold allows one spacing of endpoint resolution.
    """
    from scipy.optimize import least_squares
    from scipy.spatial import ConvexHull, QhullError, cKDTree

    try:
        points = _points(site_xy)
        if len(points) < 6:
            raise ValueError("At least six boundary sites are required")
        # Shift before circle algebra to avoid squaring large stage coordinates.
        offset = points.mean(axis=0)
        local = points - offset
        hull = local[ConvexHull(local).vertices]
        distances, _ = cKDTree(local).query(local, k=2)
        spacing = float(np.median(distances[:, 1]))
        tolerance = spacing / 2
        rng = np.random.default_rng(0)
        triples = list(combinations(range(len(hull)), 3)) if len(hull) < 20 else [
            tuple(rng.choice(len(hull), 3, replace=False)) for _ in range(1000)]
        best = None
        for triple in triples:
            try:
                centre, radius = _circle_three(hull[list(triple)])
            except ValueError:
                continue
            residuals = abs(np.linalg.norm(hull - centre, axis=1) - radius)
            retained = residuals <= tolerance
            if retained.sum() < 6 or radius < 2 * spacing:
                continue
            coverage = _arc_coverage(hull[retained], centre)
            if not _sufficient_arc(coverage, spacing, radius) or np.mean(np.linalg.norm(local - centre, axis=1) <= radius + tolerance) < 0.98:
                continue
            score = (int(retained.sum()), -float(np.median(residuals[retained])))
            if best is None or score > best[0]:
                best = score, centre, radius, retained
        if best is None:
            raise ValueError("No circular outer boundary with at least 120 degrees of arc")
        _, centre, radius, retained = best
        for _ in range(3):
            samples = hull[retained]
            result = least_squares(lambda v: np.linalg.norm(samples - v[:2], axis=1) - v[2],
                                   np.r_[centre, radius], loss="soft_l1", f_scale=spacing / 10)
            centre, radius = result.x[:2], float(result.x[2])
            retained = abs(np.linalg.norm(hull - centre, axis=1) - radius) <= tolerance
        coverage = _arc_coverage(hull[retained], centre)
        if not _sufficient_arc(coverage, spacing, radius):
            raise ValueError("Refined circular boundary has less than 120 degrees of arc")
        return ArrayBoundary("fitted", "", tuple(centre + offset), radius, coverage)
    except (ValueError, QhullError, np.linalg.LinAlgError) as error:
        return ArrayBoundary("unresolved", str(error))


def roster(fit: LatticeFit, origin, radius) -> tuple[tuple[int, int], ...]:
    """Enumerate the entire model disk, independent of observed or hidden sites.

    Use one shared array-model radius across observations. Independently noisy
    radius estimates can select different edge sites; this function does not
    conceal that difference by quantising or silently reusing an observation.
    """
    if not fit.ok:
        raise ValueError(f"Cannot build a roster from an unsuccessful fit: {fit.reason}")
    _coordinate(origin)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("Array radius must be finite and positive")
    basis = np.column_stack((fit.a1, fit.a2))
    limit = radius + fit.pitch / 4
    reach = math.ceil(limit / np.linalg.svd(basis, compute_uv=False).min())
    return tuple((q, r) for q in range(-reach, reach + 1) for r in range(-reach, reach + 1)
                 if np.linalg.norm(basis @ (q, r)) <= limit)
