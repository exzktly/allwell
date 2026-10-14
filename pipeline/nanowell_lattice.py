"""Qt-free geometry for nanowell point sets and tile ownership.

Coordinates use one consistent input unit (pixels or micrometres). Rectangles
use image x/y axes; every site receives an owner or an explicit fused index.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math

import numpy as np


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


def assign_owners(site_xy, nanowell_diameter: float, tiles, margin: float) -> OwnershipResult:
    """Assign whole disks with margin; distance ties use lexical tile-id order.

    A fused index marks a site with no containing tile, including sites outside
    all coverage. This function does not infer whether such a site can be fused;
    later coverage classification must distinguish seams from unimaged sites.
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
            fused.append(number)
    return OwnershipResult(tuple(owners), tuple(fused))


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

    Pair hypotheses minimise the median radial residual, then a median plus
    three scaled-MAD cutoff selects the consensus. At least three common,
    consensus wells are required. Residuals are reported even for rejected wells.
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
    median = np.median(residuals)
    cutoff = max(floor, median + 3 * 1.4826 * np.median(np.abs(residuals - median)))
    inliers = residuals <= cutoff
    if inliers.sum() < 3:
        return PlateRigidFit(False, "Fewer than three wells support one rigid transform")
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
